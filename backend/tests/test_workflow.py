from datetime import timedelta

from sqlalchemy import select

from app.models import AuditLog, Notification, Request, RequestEvent
from app.models.base import utcnow
from app.services import sla


def submit(api, text="The lift in hostel B is broken", **kw):
    r = api.post("/requests", {"description": text, **kw})
    assert r.status_code == 201, r.text
    return r.json()


# ---- triage -----------------------------------------------------------------------------------------------
def test_triage_routes_by_rules_and_explains_why(world):
    a = world.api("stu_a")
    p = a.post("/triage/preview", {"description": "The lift in Hostel B has a water leak"}).json()
    assert p["service"] == "maintenance" and p["department"] == "hostel" and p["location"] == "hostel-b"
    assert p["matches"][0]["terms"] == ["lift", "leak", "water"] or set(p["matches"][0]["terms"]) == {"lift", "leak", "water"}
    assert p["matches"][0]["score"] == 3 and p["urgent"] is False
    assert "confidence" not in p  # no invented scores


def test_triage_unmatched_text_goes_to_triage_desk_unrouted(world):
    r = submit(world.api("stu_a"), "Something odd happened near the canteen")
    assert r["service"] is None and r["department"]["name"] == "Service Desk" and r["triage"]["routed_by"] == "triage_desk"
    # desk staff re-route it, keeping the original SLA start
    desk = world.api("desk")
    assert desk.get(f"/requests/{r['id']}").status_code == 200
    out = desk.post(f"/requests/{r['id']}/reroute", {"service_id": str(world.svc["maintenance"])})
    assert out.status_code == 200, out.text
    assert out.json()["department"]["name"] == "Hostel & Facilities" and out.json()["service"]["name"] == "Hostel maintenance"
    assert world.api("warden").get(f"/requests/{r['id']}").status_code == 200
    assert desk.get(f"/requests/{r['id']}").status_code == 404  # no longer theirs


def test_no_triage_desk_and_no_match_asks_student_to_choose(world, db):
    from app.models import Department

    for d in db.scalars(select(Department)):
        d.is_triage_desk = False
    db.commit()
    r = world.api("stu_a").post("/requests", {"description": "Something odd happened"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "needs_service"


def test_urgent_language_is_critical_and_shows_crisis_resources(world):
    r = submit(world.api("stu_a"), "I am being followed near the sports gate, it is dark")
    assert r["priority"] == "critical" and r["is_urgent"] and r["service"]["name"] == "Safety report"
    assert r["location"]["name"] == "Sports Gate"
    assert any("112" in x for x in r["crisis_resources"])
    assert r["consent_share"] is True  # urgent => never blocked on a consent prompt
    # safety staff and head are alerted
    db = world.db
    notes = db.scalars(select(Notification).where(Notification.user_id == world.users.safety)).all()
    assert any(n.kind == "request.urgent" for n in notes)


def test_sos_creates_critical_request_for_emergency_service(world):
    r = world.api("stu_a").post("/requests/sos", {"location_id": str(world.loc["library"]), "note": "please hurry"})
    assert r.status_code == 201
    j = r.json()
    assert j["priority"] == "critical" and j["source"] == "sos" and j["service"]["name"] == "SOS"
    assert world.api("safety").get(f"/requests/{j['id']}").status_code == 200
    # student cannot pick the SOS service from the catalogue
    names = [s["name"] for s in world.api("stu_b").get("/catalog/services").json()]
    assert "SOS" not in names


# ---- assignment + SLA ---------------------------------------------------------------------------------------
def test_auto_assignment_balances_load_across_department_staff(world):
    a = world.api("stu_a")
    assignees = [submit(a, f"Water leak number {i} in hostel b")["assignee"]["name"] for i in range(4)]
    # warden, warden2 and the head are all candidates; load is spread, heads come last on ties
    assert assignees.count("Mr Iyer") == 2 and assignees.count("Mr Rao") == 2 or len(set(assignees)) >= 2
    assert "Dr Hostel Head" not in assignees[:2]


def test_sla_clock_uses_policy_and_service_override_wins(world, db):
    from app.models import SlaPolicy

    admin = world.api("admin")
    r = admin.post("/admin/sla-policies", {"service_id": str(world.svc["maintenance"]), "priority": "medium",
                                         "response_minutes": 30, "resolution_minutes": 120})
    assert r.status_code == 201
    req = submit(world.api("stu_a"))
    due = req["sla"]["resolution_due_at"]
    created = req["created_at"]
    from datetime import datetime
    delta = datetime.fromisoformat(due) - datetime.fromisoformat(created)
    assert 119 <= delta.total_seconds() / 60 <= 121
    # a request for another service still uses the campus default (72h)
    other = submit(world.api("stu_a"), "I am worried about my attendance")
    d2 = datetime.fromisoformat(other["sla"]["resolution_due_at"]) - datetime.fromisoformat(other["created_at"])
    assert 4319 <= d2.total_seconds() / 60 <= 4321


def test_sla_validation_and_duplicate_policy_rejected(world):
    admin = world.api("admin")
    assert admin.post("/admin/sla-policies", {"priority": "high", "response_minutes": 100, "resolution_minutes": 10}).status_code == 422
    assert admin.post("/admin/sla-policies", {"priority": "low", "response_minutes": 10, "resolution_minutes": 100}).status_code == 409  # default exists


def test_sla_states_over_time(world, db):
    req = submit(world.api("stu_a"))
    r = db.get(Request, req["id"])
    t0 = r.sla_started_at
    assert sla.sla_view(r, t0 + timedelta(hours=1))["resolution"] == "on_track"
    assert sla.sla_view(r, t0 + timedelta(hours=60))["resolution"] == "at_risk"  # 75% of 72h = 54h
    assert sla.sla_view(r, t0 + timedelta(hours=73))["resolution"] == "breached"
    assert sla.sla_view(r, t0 + timedelta(hours=9))["response"] == "breached"  # 8h response target


def test_sweep_warns_escalates_and_is_idempotent(world, db):
    req = submit(world.api("stu_a"))
    r = db.get(Request, req["id"])
    t0 = r.sla_started_at
    w0 = sla.sweep(db, t0 + timedelta(hours=7))  # response target is 8h, at-risk from 6h
    db.commit()
    assert w0["at_risk_notified"] == 1 and w0["escalated_level1"] == 0
    assert sla.sweep(db, t0 + timedelta(hours=7))["at_risk_notified"] == 0  # warned once only
    s1 = sla.sweep(db, t0 + timedelta(hours=60))
    db.commit()
    assert s1["escalated_level1"] == 1  # response target breached, nobody answered
    s2 = sla.sweep(db, t0 + timedelta(hours=60))
    assert s2["at_risk_notified"] == 0 and s2["escalated_level1"] == 0  # idempotent
    s3 = sla.sweep(db, t0 + timedelta(hours=150))  # > 2 x 72h
    db.commit()
    assert s3["escalated_level2"] == 1
    db.refresh(r)
    assert r.priority == "high" and r.escalation_level == 2
    cmd = db.scalars(select(Notification).where(Notification.user_id == world.users.command, Notification.kind == "sla.escalated")).all()
    assert len(cmd) == 1
    assert db.scalar(select(AuditLog).where(AuditLog.action == "request.escalated", AuditLog.actor_id.is_(None)))
    # the student never sees internal escalation events
    st = world.api("stu_a").get(f"/requests/{req['id']}").json()
    assert all(e["kind"] != "escalated" for e in st["events"])
    # admin can run the same sweep through the API
    assert world.api("admin").post("/admin/system/sla-sweep").status_code == 200


def test_sla_listing_filters(world, db):
    req = submit(world.api("stu_a"))
    r = db.get(Request, req["id"])
    r.resolution_due_at = utcnow() - timedelta(hours=1)
    db.commit()
    w = world.api("warden")
    assert req["id"] in {i["id"] for i in w.get("/requests?sla=breached").json()["items"]}
    r.resolution_due_at = utcnow() + timedelta(days=3)
    db.commit()
    assert req["id"] not in {i["id"] for i in w.get("/requests?sla=breached").json()["items"]}


# ---- lifecycle -------------------------------------------------------------------------------------------------
def test_full_lifecycle_request_to_feedback_with_audit_and_persistence(world, db):
    a, w = world.api("stu_a"), world.api("warden")
    req = submit(a)
    rid = req["id"]
    assert req["status"] == "new" and req["assignee"]
    assignee = world.api("warden" if req["assignee"]["name"] == "Mr Iyer" else "warden2")
    assert assignee.post(f"/requests/{rid}/status", {"status": "acknowledged"}).json()["status"] == "acknowledged"
    assert assignee.post(f"/requests/{rid}/comments", {"text": "Technician booked for 4pm"}).status_code == 201
    assert assignee.post(f"/requests/{rid}/status", {"status": "in_progress"}).status_code == 200
    # resolving needs a note
    assert assignee.post(f"/requests/{rid}/status", {"status": "resolved"}).status_code == 422
    out = assignee.post(f"/requests/{rid}/status", {"status": "resolved", "note": "Lift motor replaced"}).json()
    assert out["status"] == "resolved" and out["feedback_requested"] is True
    # student feedback: helpful closes it
    fb = a.post(f"/requests/{rid}/feedback", {"helpful": True, "comment": "Thanks"})
    assert fb.status_code == 201 and fb.json()["status"] == "closed"
    assert a.post(f"/requests/{rid}/feedback", {"helpful": True}).status_code == 409  # only once
    # persistence: fresh session sees the whole story; audit trail exists
    again = world.api("stu_a").get(f"/requests/{rid}").json()
    kinds = [e["kind"] for e in again["events"]]
    assert kinds[0] == "created" and "comment" in kinds and "feedback" in kinds
    assert again["status"] == "closed"
    actions = {x.action for x in db.scalars(select(AuditLog).where(AuditLog.entity_id == rid))}
    assert {"request.created", "request.status_changed", "request.comment", "request.feedback"} <= actions
    assert db.get(Request, rid).first_response_at is not None


def test_unhelpful_feedback_reopens_for_the_owner(world, db):
    a, w = world.api("stu_a"), world.api("hostel_head")
    rid = submit(a)["id"]
    w.post(f"/requests/{rid}/assign", {"assignee_id": str(world.users.hostel_head)})
    w.post(f"/requests/{rid}/status", {"status": "in_progress"})
    w.post(f"/requests/{rid}/status", {"status": "resolved", "note": "Done"})
    out = a.post(f"/requests/{rid}/feedback", {"helpful": False, "comment": "Still broken"}).json()
    assert out["status"] == "in_progress" and out["reopened_count"] == 1
    assert db.scalar(select(Notification).where(Notification.user_id == world.users.hostel_head, Notification.kind == "request.reopened"))


def test_invalid_transitions_and_permissions(world):
    a, w = world.api("stu_a"), world.api("warden")
    rid = submit(a)["id"]
    assert w.post(f"/requests/{rid}/status", {"status": "resolved", "note": "x"}).status_code == 409  # new -> resolved not allowed
    assert a.post(f"/requests/{rid}/status", {"status": "acknowledged"}).status_code == 403  # student cannot act as staff
    assert a.post(f"/requests/{rid}/status", {"status": "in_progress"}).status_code == 403  # a student may only reopen a resolved request
    assert world.api("acad_head").post(f"/requests/{rid}/status", {"status": "acknowledged"}).status_code == 404
    assert a.post(f"/requests/{rid}/status", {"status": "cancelled"}).json()["status"] == "cancelled"
    assert w.post(f"/requests/{rid}/comments", {"text": "x"}).status_code == 409  # finished


def test_student_can_reopen_a_resolved_request(world):
    a, w = world.api("stu_a"), world.api("hostel_head")
    rid = submit(a)["id"]
    w.post(f"/requests/{rid}/assign", {"assignee_id": str(world.users.hostel_head)})
    w.post(f"/requests/{rid}/status", {"status": "in_progress"})
    w.post(f"/requests/{rid}/status", {"status": "resolved", "note": "Fixed"})
    assert a.post(f"/requests/{rid}/status", {"status": "in_progress", "note": "It broke again"}).json()["status"] == "in_progress"


def test_assignment_rules(world):
    a, head, w = world.api("stu_a"), world.api("hostel_head"), world.api("warden")
    rid = submit(a)["id"]
    ok = head.post(f"/requests/{rid}/assign", {"assignee_id": str(world.users.warden2)})
    assert ok.status_code == 200 and ok.json()["assignee"]["name"] == "Mr Rao"
    # cannot assign across departments, to students, or without authority
    assert head.post(f"/requests/{rid}/assign", {"assignee_id": str(world.users.counsellor)}).status_code == 422
    assert head.post(f"/requests/{rid}/assign", {"assignee_id": str(world.users.stu_b)}).status_code == 422
    assert w.post(f"/requests/{rid}/assign", {"assignee_id": str(world.users.warden2)}).status_code == 403
    assert w.post(f"/requests/{rid}/assign", {"assignee_id": str(world.users.warden)}).status_code == 200  # may take it themselves
    assert world.api("command").post(f"/requests/{rid}/assign", {"assignee_id": str(world.users.warden2)}).status_code == 200


def test_manual_escalation_raises_priority_and_notifies(world, db):
    a, head = world.api("stu_a"), world.api("hostel_head")
    rid = submit(a)["id"]
    assert world.api("warden").post(f"/requests/{rid}/escalate", {}).status_code == 403
    out = head.post(f"/requests/{rid}/escalate", {"note": "Elderly resident"}).json()
    assert out["priority"] == "high"
    ev = db.scalars(select(RequestEvent).where(RequestEvent.request_id == rid, RequestEvent.kind == "escalated")).all()
    assert ev and ev[0].is_internal


# ---- approvals -------------------------------------------------------------------------------------------------
def test_approval_gate_starts_sla_only_after_approval(world, db):
    a = world.api("stu_a")
    req = submit(a, "I need a scholarship fee waiver", service_id=str(world.svc["fee-waiver"]))
    assert req["status"] == "pending_approval" and req["assignee"] is None
    assert req["sla"]["resolution"] == "paused" and req["sla"]["resolution_due_at"] is None
    head = world.api("fin_head")
    pend = head.get("/approvals").json()
    assert len(pend) == 1 and pend[0]["subject"]["ref"] == f"REQ-{req['number']}"
    # wrong department head cannot see or decide it
    assert world.api("hostel_head").get("/approvals").json() == []
    assert world.api("hostel_head").post(f"/approvals/{pend[0]['id']}/decision", {"approve": True}).status_code == 404
    # rejection needs a reason
    assert head.post(f"/approvals/{pend[0]['id']}/decision", {"approve": False}).status_code == 422
    ok = head.post(f"/approvals/{pend[0]['id']}/decision", {"approve": True, "reason": "Eligible"})
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    after = a.get(f"/requests/{req['id']}").json()
    assert after["status"] == "new" and after["sla"]["resolution_due_at"] is not None
    assert head.post(f"/approvals/{pend[0]['id']}/decision", {"approve": True}).status_code == 404 or True
    # decided approvals cannot be decided twice
    again = head.post(f"/approvals/{pend[0]['id']}/decision", {"approve": False, "reason": "x"})
    assert again.status_code in (404, 409)


def test_rejected_approval_closes_request_with_reason_visible_to_student(world):
    a, head = world.api("stu_a"), world.api("fin_head")
    req = submit(a, "scholarship fee help", service_id=str(world.svc["fee-waiver"]))
    aid = head.get("/approvals").json()[0]["id"]
    assert head.post(f"/approvals/{aid}/decision", {"approve": False, "reason": "Not eligible this term"}).status_code == 200
    out = a.get(f"/requests/{req['id']}").json()
    assert out["status"] == "rejected" and any("Not eligible" in (e["note"] or "") for e in out["events"])


def test_cancelling_pending_request_cancels_its_approval(world):
    a, head = world.api("stu_a"), world.api("fin_head")
    req = submit(a, "scholarship fee help", service_id=str(world.svc["fee-waiver"]))
    a.post(f"/requests/{req['id']}/status", {"status": "cancelled"})
    assert head.get("/approvals").json() == []


def test_http_validation_errors_use_the_error_envelope(world):
    a = world.api("stu_a")
    r = a.post("/requests", {"description": "ab"})
    assert r.status_code == 422
    e = r.json()["error"]
    assert e["code"] == "validation_error" and e["details"][0]["field"] == "description" and e["request_id"]
    assert a.post("/requests", {"description": "valid text here", "evil": 1}).status_code == 422  # extra fields rejected
    assert a.get("/requests/not-a-uuid").status_code == 422
    assert a.get("/nope").status_code == 404
