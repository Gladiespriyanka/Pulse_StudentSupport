from datetime import timedelta

from sqlalchemy import select

from app.models import AccessRequest, AuditLog, Case, Notification
from app.models.base import utcnow
from tests.test_workflow import submit

REASONS_OK = "known_temporary_event"


def trigger_case(world, who="stu_a"):
    a = world.api(who)
    submit(a, "The lift in hostel B is broken")
    submit(a, "I am worried about my attendance")
    submit(a, "I feel stressed and overwhelmed", consent_share=True)
    return a


def consent_all(world, a, mentor=True, depts=("wellbeing",)):
    r = a.put("/consents/me", {"mentor": mentor, "department_ids": [str(world.dept[d]) for d in depts]})
    assert r.status_code == 200, r.text
    return r.json()


def case_id_for(api):
    items = api.get("/cases").json()["items"]
    return items[0]["id"] if items else None


def test_rule_needs_enough_requests_across_enough_departments(world):
    a = world.api("stu_a")
    for _ in range(3):
        submit(a, "Water leak in hostel b")  # 3 requests, 1 department
    assert a.get("/cases").json()["items"] == []
    submit(a, "I am worried about my attendance")  # 2 departments
    assert a.get("/cases").json()["items"] == []
    submit(a, "I feel stressed", consent_share=True)  # 3 departments
    items = a.get("/cases").json()["items"]
    assert len(items) == 1 and items[0]["status"] == "suggested" and items[0]["origin"] == "rule"


def test_suggestion_is_invisible_to_humans_until_student_consents(world):
    a = trigger_case(world)
    cid = case_id_for(a)
    for who in ("mentor", "counsellor", "warden", "hostel_head"):
        api = world.api(who)
        assert api.get("/cases").json()["items"] == [], who
        assert api.get(f"/cases/{cid}").status_code == 404, who
    # student sees evidence in plain terms: counts and departments only, no free text, no score
    ev = a.get(f"/cases/{cid}").json()["evidence"]
    assert ev["request_count"] == 3 and ev["department_count"] == 3 and ev["rule"] == "multi_area_pattern"
    assert set(ev["departments"]) == {"Hostel & Facilities", "Academic Support", "Counselling"}
    assert "lift" not in str(ev).lower() and "score" not in str(ev).lower()
    # student was offered support, nobody else was told
    kinds = {n.kind for n in world.db.scalars(select(Notification).where(Notification.user_id == world.users.stu_a))}
    assert "case.offer" in kinds
    assert not world.db.scalars(select(Notification).where(Notification.kind == "case.suggested")).all()


def test_consent_screen_state_is_persisted_and_audited(world, db):
    a = world.api("stu_a")
    assert a.get("/auth/me").json()["consent_onboarded"] is False
    c0 = a.get("/consents/me").json()
    assert c0["mentor_allowed"] is False and c0["department_ids"] == [] and c0["mentor"]["name"] == "Prof Nair"
    consent_all(world, a)
    assert world.api("stu_a").get("/auth/me").json()["consent_onboarded"] is True  # survives re-login
    c1 = world.api("stu_a").get("/consents/me").json()
    assert c1["mentor_allowed"] is True and c1["department_ids"] == [str(world.dept["wellbeing"])]
    assert db.scalar(select(AuditLog).where(AuditLog.action == "consent.updated", AuditLog.subject_user_id == world.users.stu_a))
    assert a.put("/consents/me", {"mentor": False, "department_ids": [str(world.users.stu_a)]}).status_code == 422  # unknown dept


def test_consented_reviewers_see_suggestion_and_decide(world):
    a = trigger_case(world)
    consent_all(world, a)
    cid = case_id_for(a)
    co, mentor = world.api("counsellor"), world.api("mentor")
    assert co.get(f"/cases/{cid}").status_code == 200 and mentor.get(f"/cases/{cid}").status_code == 200
    assert world.api("warden").get(f"/cases/{cid}").status_code == 404  # hostel not consented
    # dismissal needs a listed reason and is final for this window
    assert mentor.post(f"/cases/{cid}/dismiss", {"reason": "because"}).status_code == 422
    assert world.api("warden").post(f"/cases/{cid}/dismiss", {"reason": REASONS_OK}).status_code in (403, 404)
    assert mentor.post(f"/cases/{cid}/dismiss", {"reason": REASONS_OK}).json()["status"] == "dismissed"
    submit(a, "Another water leak in hostel b")
    assert [c for c in a.get("/cases").json()["items"] if c["status"] == "suggested"] == []  # no immediate re-suggestion


def test_confirm_team_building_and_consent_gates(world):
    a = trigger_case(world)
    consent_all(world, a, mentor=False, depts=("wellbeing",))
    cid = case_id_for(a)
    co = world.api("counsellor")
    d = co.post(f"/cases/{cid}/confirm").json()
    assert d["status"] == "open" and d["owner"]["name"] == "Ms Kapoor"
    # students are told, and may accept or decline
    assert world.db.scalar(select(Notification).where(Notification.user_id == world.users.stu_a, Notification.kind == "case.confirmed"))
    # mentor/warden not consented -> cannot be added
    for uid in (world.users.mentor, world.users.warden):
        r = co.post(f"/cases/{cid}/participants", {"user_id": str(uid)})
        assert r.status_code == 403 and r.json()["error"]["code"] == "no_consent"
    cands = {p["name"] for p in co.get(f"/cases/{cid}/candidates").json()}
    assert "Prof Nair" not in cands and "Mr Iyer" not in cands
    # student widens consent: now they can be added
    consent_all(world, a, mentor=True, depts=("wellbeing", "hostel"))
    assert co.post(f"/cases/{cid}/participants", {"user_id": str(world.users.mentor)}).status_code == 201
    assert co.post(f"/cases/{cid}/participants", {"user_id": str(world.users.warden)}).status_code == 201
    assert co.post(f"/cases/{cid}/participants", {"user_id": str(world.users.warden)}).status_code == 409
    assert co.post(f"/cases/{cid}/participants", {"user_id": str(world.users.stu_b)}).status_code == 422
    # primary owner hand-over: one person coordinates
    assert co.post(f"/cases/{cid}/owner", {"user_id": str(world.users.mentor)}).json()["owner"]["name"] == "Prof Nair"
    assert co.delete(f"/cases/{cid}/participants/{world.users.mentor}").status_code == 409  # owner cannot be removed
    # participants see linked requests but only the text they are entitled to
    wd = world.api("warden").get(f"/cases/{cid}").json()
    by = {r["department"]: r for r in wd["requests"]}
    assert by["Hostel & Facilities"]["description"] and by["Academic Support"]["description"] is None and by["Academic Support"]["restricted"]
    assert by["Counselling"]["description"] is None


def test_revoking_consent_removes_access_immediately(world):
    a = trigger_case(world)
    consent_all(world, a, mentor=True, depts=("wellbeing", "hostel"))
    cid = case_id_for(a)
    co = world.api("counsellor")
    co.post(f"/cases/{cid}/confirm")
    co.post(f"/cases/{cid}/participants", {"user_id": str(world.users.warden)})
    w = world.api("warden")
    assert w.get(f"/cases/{cid}").status_code == 200
    consent_all(world, a, mentor=True, depts=("wellbeing",))  # revoke hostel
    assert w.get(f"/cases/{cid}").status_code == 404 and w.get("/cases").json()["items"] == []
    assert w.post(f"/cases/{cid}/messages", {"text": "hi"}).status_code == 404
    consent_all(world, a, mentor=False, depts=())  # revoke everything
    assert co.get(f"/cases/{cid}").status_code == 404


def test_thread_interventions_outcomes_and_student_feedback_loop(world):
    a = trigger_case(world)
    consent_all(world, a)
    cid = case_id_for(a)
    co, mentor = world.api("counsellor"), world.api("mentor")
    co.post(f"/cases/{cid}/confirm")
    co.post(f"/cases/{cid}/participants", {"user_id": str(world.users.mentor)})
    # thread: participants and student can write; outsiders cannot
    assert co.post(f"/cases/{cid}/messages", {"text": "Let's offer a relaxed chat"}).status_code == 201
    assert a.post(f"/cases/{cid}/messages", {"text": "Thanks, Thursday works"}).status_code == 201
    assert world.api("warden").post(f"/cases/{cid}/messages", {"text": "hi"}).status_code == 404
    thread = mentor.get(f"/cases/{cid}").json()["messages"]
    assert [m["author"]["role"] for m in thread] == ["counsellor", "student"]
    # intervention task
    t = co.post(f"/cases/{cid}/tasks", {"title": "Offer a voluntary check-in", "assignee_id": str(world.users.mentor)})
    assert t.status_code == 201
    tid = t.json()["tasks"][0]["id"]
    assert world.api("hostel_head").patch(f"/tasks/{tid}", {"status": "done"}).status_code == 404
    assert mentor.patch(f"/tasks/{tid}", {"status": "done"}).status_code == 422  # outcome required
    done = mentor.patch(f"/tasks/{tid}", {"status": "done", "outcome": "helped", "outcome_note": "Met for tea"})
    assert done.status_code == 200 and done.json()["outcome"] == "helped"
    # student sees the action but not staff notes
    sv = a.get(f"/cases/{cid}").json()
    assert sv["tasks"][0]["title"] == "Offer a voluntary check-in" and "outcome_note" not in sv["tasks"][0]
    # student says it did not help -> action reopens for the team
    fb = a.post(f"/tasks/{tid}/feedback", {"helpful": False})
    assert fb.status_code == 201 and fb.json()["status"] == "todo" and fb.json()["outcome"] is None
    assert a.post(f"/tasks/{tid}/feedback", {"helpful": True}).status_code in (409, 422)
    # monitoring then close (tasks still open are cancelled)
    assert co.post(f"/cases/{cid}/status", {"status": "monitoring"}).json()["status"] == "monitoring"
    closed = co.post(f"/cases/{cid}/close", {"outcome": "stabilised", "note": "Back to routine"}).json()
    assert closed["status"] == "closed" and closed["tasks"][0]["status"] == "cancelled"
    assert co.post(f"/cases/{cid}/messages", {"text": "x"}).status_code == 409


def test_student_declining_outreach_blocks_new_outreach_tasks(world):
    a = trigger_case(world)
    consent_all(world, a)
    cid = case_id_for(a)
    co = world.api("counsellor")
    co.post(f"/cases/{cid}/confirm")
    assert a.post(f"/cases/{cid}/response", {"response": "declined"}).json()["student_response"] == "declined"
    r = co.post(f"/cases/{cid}/tasks", {"title": "Call the student"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "student_declined"
    assert a.post(f"/cases/{cid}/close", {}).json()["outcome"] == "declined"  # student can end it


def test_student_transparency_access_log_shows_who_looked(world):
    a = trigger_case(world)
    consent_all(world, a)
    cid = case_id_for(a)
    co = world.api("counsellor")
    co.post(f"/cases/{cid}/confirm")
    co.get(f"/cases/{cid}")
    log = a.get("/me/access-log").json()
    assert any(x["by"] == "Ms Kapoor" and x["action"] == "case.viewed" for x in log)
    assert world.api("stu_b").get("/me/access-log").json() == []


def test_break_glass_requires_student_approval_expires_and_is_audited(world, db):
    a = world.api("stu_a")
    acad = submit(a, "I am worried about my attendance")["id"]
    co = world.api("counsellor")
    assert co.get(f"/requests/{acad}").status_code == 404
    assert co.post("/access-requests", {"student_id": str(world.users.stu_a), "reason": "short"}).status_code == 422
    ar = co.post("/access-requests", {"student_id": str(world.users.stu_a), "reason": "Welfare check after missed classes"})
    assert ar.status_code == 201 and ar.json()["status"] == "pending"
    assert co.post("/access-requests", {"student_id": str(world.users.stu_a), "reason": "Welfare check again please"}).status_code == 409
    assert co.get(f"/requests/{acad}").status_code == 404  # pending is not access
    arid = ar.json()["id"]
    # only the student can decide; others cannot even see it
    assert world.api("stu_b").post(f"/access-requests/{arid}/decision", {"approve": True}).status_code == 404
    assert co.post(f"/access-requests/{arid}/decision", {"approve": True}).status_code == 403
    assert a.post(f"/access-requests/{arid}/decision", {"approve": True}).json()["status"] == "approved"
    got = co.get(f"/requests/{acad}")
    assert got.status_code == 200 and got.json()["description"]
    # every use is flagged for the student
    assert any(x["break_glass"] for x in a.get("/me/access-log").json())
    # still not everything: other students stay invisible
    assert co.get("/requests").json()["total"] >= 1
    # expiry
    row = db.get(AccessRequest, arid)
    row.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    assert co.get(f"/requests/{acad}").status_code == 404
    # fresh grant can be revoked by the student at any time
    ar2 = co.post("/access-requests", {"student_id": str(world.users.stu_a), "reason": "Second welfare check needed"}).json()
    a.post(f"/access-requests/{ar2['id']}/decision", {"approve": True})
    assert co.get(f"/requests/{acad}").status_code == 200
    assert a.post(f"/access-requests/{ar2['id']}/revoke").json()["status"] == "revoked"
    assert co.get(f"/requests/{acad}").status_code == 404
    # denied
    ar3 = co.post("/access-requests", {"student_id": str(world.users.stu_a), "reason": "Third attempt for welfare"}).json()
    assert a.post(f"/access-requests/{ar3['id']}/decision", {"approve": False}).json()["status"] == "denied"
    assert co.get(f"/requests/{acad}").status_code == 404


def test_rule_quality_is_measured_from_human_decisions(world):
    for who in ("stu_a", "stu_b"):
        a = trigger_case(world, who)
    consent_all(world, world.api("stu_a"))
    consent_all(world, world.api("stu_b"), mentor=False)
    co = world.api("counsellor")
    c_a = case_id_for(world.api("stu_a"))
    c_b = case_id_for(world.api("stu_b"))
    co.post(f"/cases/{c_a}/confirm")
    co.post(f"/cases/{c_b}/dismiss", {"reason": "not_a_concern"})
    care = world.api("command").get("/reports/summary").json()["care"]
    rs = care["rule_suggestions"]
    assert rs["confirmed"] == 1 and rs["dismissed"] == 1 and rs["confirmation_rate_pct"] == 50.0 and rs["dismiss_reasons"] == {"not_a_concern": 1}
    assert care["interventions"]["masked"] is True  # too few outcomes to publish


def test_manual_case_requires_consent_and_one_active_case(world):
    co, a = world.api("counsellor"), world.api("stu_a")
    body = {"student_id": str(world.users.stu_a), "title": "Check-in after exam stress"}
    assert co.post("/cases", body).status_code == 403
    consent_all(world, a, mentor=False)
    r = co.post("/cases", body)
    assert r.status_code == 201 and r.json()["origin"] == "manual" and r.json()["status"] == "open"
    assert co.post("/cases", body).status_code == 409
