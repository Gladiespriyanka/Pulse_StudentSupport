import uuid

import pytest

ROLES = ["stu_a", "warden", "hostel_head", "counsellor", "mentor", "command", "admin"]
ZERO = str(uuid.uuid4())
STAFF3 = {"warden", "hostel_head", "counsellor"}

# (method, path, body, roles allowed past the permission gate)
MATRIX = [
    ("GET", "/admin/users", None, {"admin"}),
    ("GET", "/admin/roles", None, {"admin"}),
    ("GET", "/admin/audit-logs", None, {"admin"}),
    ("GET", "/admin/departments", None, {"admin"}),
    ("GET", "/admin/services", None, {"admin"}),
    ("GET", "/admin/sla-policies", None, {"admin"}),
    ("GET", "/admin/triage-rules", None, {"admin"}),
    ("POST", "/admin/system/sla-sweep", None, {"admin"}),
    ("GET", "/admin/imports/users/template", None, {"admin"}),
    ("GET", "/reports/summary", None, {"warden", "hostel_head", "counsellor", "command", "admin"}),
    ("GET", "/reports/heatmap", None, {"command", "admin"}),
    ("GET", "/reports/briefing", None, {"command", "admin"}),
    ("GET", "/approvals", None, {"hostel_head", "command"}),
    ("GET", "/incidents", None, {"warden", "hostel_head", "command"}),
    ("GET", "/problems", None, {"warden", "hostel_head", "command"}),
    ("GET", "/changes", None, {"warden", "hostel_head", "command"}),
    ("GET", "/intelligence/incident-clusters", None, {"warden", "hostel_head", "command"}),
    ("GET", "/cases", None, {"stu_a", "warden", "hostel_head", "counsellor", "mentor"}),
    ("POST", f"/cases/{ZERO}/confirm", None, {"counsellor", "mentor"}),
    ("GET", "/consents/me", None, {"stu_a"}),
    ("GET", "/me/access-log", None, {"stu_a"}),
    ("POST", "/requests", {"description": "lift broken in hostel b"}, {"stu_a"}),
    ("POST", "/requests/sos", {}, {"stu_a"}),
    ("POST", "/triage/preview", {"description": "lift"}, {"stu_a"}),
    ("GET", "/catalog/assets", None, {"warden", "hostel_head", "command", "admin"}),
    ("GET", "/requests", None, {"stu_a", "warden", "hostel_head", "counsellor", "command"}),
    ("GET", "/tasks", None, {"stu_a", "warden", "hostel_head", "counsellor", "mentor", "command"}),
    ("POST", "/access-requests", {"student_id": ZERO, "reason": "needed for welfare check"}, {"warden", "hostel_head", "counsellor", "mentor"}),
    ("POST", f"/incidents", {"title": "Lift out of service"}, {"warden", "hostel_head", "command"}),
    ("POST", "/knowledge", {"title": "How to", "body": "Do the thing"}, {"hostel_head"}),
]


@pytest.mark.parametrize("method,path,body,allowed", MATRIX, ids=[f"{m} {p.replace(ZERO, '{id}')}" for m, p, _, _ in MATRIX])
def test_permission_matrix(world, method, path, body, allowed):
    for role in ROLES:
        api = world.api(role)
        r = api.c.request(method, "/api/v1" + path, json=body, headers={"X-CSRF-Token": api.c.cookies.get("pulse_csrf")})
        if role in allowed:
            assert r.status_code not in (401, 403), f"{role} should pass the gate for {method} {path}: {r.status_code} {r.text[:150]}"
        else:
            assert r.status_code == 403, f"{role} must be denied {method} {path}, got {r.status_code}"


def test_every_denial_is_audited(world, db):
    from sqlalchemy import select
    from app.models import AuditLog

    world.api("stu_a").get("/admin/users")
    row = db.scalar(select(AuditLog).where(AuditLog.action == "authz.denied"))
    assert row and row.outcome == "denied" and row.actor_id == world.users.stu_a


# ---- row-level privacy -------------------------------------------------------------------------------
def _submit(api, text="The lift in hostel B is broken", **kw):
    r = api.post("/requests", {"description": text, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_student_cannot_see_or_touch_another_students_request(world):
    a, b = world.api("stu_a"), world.api("stu_b")
    rid = _submit(a)["id"]
    assert b.get(f"/requests/{rid}").status_code == 404  # not 403: existence is not revealed
    assert all(i["id"] != rid for i in b.get("/requests").json()["items"])
    assert b.post(f"/requests/{rid}/comments", {"text": "hi"}).status_code == 404
    assert b.post(f"/requests/{rid}/status", {"status": "cancelled"}).status_code == 404
    assert b.post(f"/requests/{rid}/feedback", {"helpful": True}).status_code == 404
    assert b.post(f"/requests/{rid}/consent", {"share": False}).status_code == 404


def test_department_staff_only_see_their_department(world):
    a = world.api("stu_a")
    maint = _submit(a, "The lift in hostel B is broken")
    acad = _submit(a, "I am worried about my attendance and assignment")
    assert maint["department"]["name"] == "Hostel & Facilities" and acad["department"]["name"] == "Academic Support"
    w = world.api("warden")
    ids = {i["id"] for i in w.get("/requests").json()["items"]}
    assert maint["id"] in ids and acad["id"] not in ids
    assert w.get(f"/requests/{acad['id']}").status_code == 404
    assert world.api("acad_head").get(f"/requests/{acad['id']}").status_code == 200


def test_command_sees_metadata_but_never_text(world):
    a = world.api("stu_a")
    r = _submit(a, "The lift in hostel B is broken, my private note")
    c = world.api("command")
    lst = c.get("/requests").json()["items"]
    row = next(i for i in lst if i["id"] == r["id"])
    assert row["restricted"] is True and "description" not in row and "requester" not in row
    assert "private note" not in row["title"] and "lift" not in row["title"].lower()
    d = c.get(f"/requests/{r['id']}").json()
    assert d["restricted"] is True and "description" not in d and "events" not in d
    assert "private note" not in str(d)


def test_admin_cannot_read_requests_at_all(world):
    _submit(world.api("stu_a"))
    assert world.api("admin").get("/requests").status_code == 403


def test_mentor_cannot_read_student_requests(world):
    r = _submit(world.api("stu_a"), "I am worried about my attendance")
    m = world.api("mentor")
    assert m.get("/requests").status_code == 403
    assert m.get(f"/requests/{r['id']}").status_code == 404


def test_sensitive_request_hidden_from_department_without_consent(world):
    a = world.api("stu_a")
    r = _submit(a, "I feel very stressed and overwhelmed", consent_share=False)
    assert r["service"]["name"] == "Counselling"
    c = world.api("counsellor")
    assert r["id"] not in {i["id"] for i in c.get("/requests").json()["items"]}
    assert c.get(f"/requests/{r['id']}").status_code == 404
    # student changes their mind -> counsellor can now see it
    assert a.post(f"/requests/{r['id']}/consent", {"share": True}).status_code == 200
    assert c.get(f"/requests/{r['id']}").status_code == 200
    # and can withdraw again
    a.post(f"/requests/{r['id']}/consent", {"share": False})
    assert c.get(f"/requests/{r['id']}").status_code == 404


def test_sensitive_service_requires_an_explicit_consent_choice(world):
    a = world.api("stu_a")
    r = a.post("/requests", {"description": "I feel stressed and lonely"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "consent_required"
    assert a.post("/requests", {"description": "I feel stressed and lonely", "consent_share": True}).status_code == 201


def test_internal_notes_never_reach_the_student(world):
    a, w = world.api("stu_a"), world.api("warden")
    rid = _submit(a)["id"]
    w.post(f"/requests/{rid}/status", {"status": "acknowledged"})
    assert w.post(f"/requests/{rid}/comments", {"text": "internal: tenant is a repeat reporter", "internal": True}).status_code == 201
    assert w.post(f"/requests/{rid}/comments", {"text": "We are sending a technician today"}).status_code == 201
    student_view = a.get(f"/requests/{rid}").json()
    assert "repeat reporter" not in str(student_view) and "technician" in str(student_view)
    assert "repeat reporter" in str(w.get(f"/requests/{rid}").json())
    assert a.post(f"/requests/{rid}/comments", {"text": "x", "internal": True}).status_code == 403


def test_peer_concern_is_anonymous_and_reaches_counsellors(world, db):
    from sqlalchemy import select
    from app.models import AuditLog, Request

    a = world.api("stu_a")
    assert a.post("/requests/peer-concern", {"description": "My friend has stopped attending and seems very low"}).status_code == 201
    req = db.scalar(select(Request).where(Request.source == "peer"))
    assert req.requester_id is None
    assert world.api("counsellor").get("/requests").json()["items"][0]["id"] == str(req.id)
    audit_rows = db.scalars(select(AuditLog).where(AuditLog.action == "request.peer_concern")).all()
    assert audit_rows and all(r.actor_id is None for r in audit_rows)
    assert all(str(world.users.stu_a) not in str(r.details) for r in audit_rows)
    assert all(r.ip is None and r.request_id is None for r in audit_rows)  # not even the network address is kept


def test_free_text_pii_is_redacted_before_storage(world, db):
    from sqlalchemy import select, text
    from app.models import Request

    r = _submit(world.api("stu_a"), "Lift broken. Call me on 9876543210 or mail me@x.com")
    assert "9876543210" not in r["description"] and "[phone]" in r["description"] and "[email]" in r["description"]


def test_free_text_is_encrypted_at_rest(world, db):
    from sqlalchemy import text

    _submit(world.api("stu_a"), "Highly sensitive text about my roommate")
    raw = db.execute(text("select description from requests")).scalar()
    assert raw.startswith("enc1:") and "sensitive" not in raw


def test_notifications_are_private(world):
    a = world.api("stu_a")
    _submit(a)
    w = world.api("warden")
    mine = w.get("/notifications").json()
    assert mine["unread"] >= 0
    other = world.api("stu_b").get("/notifications").json()
    assert other["items"] == []
    nid = (mine["items"] or [{"id": ZERO}])[0]["id"]
    # marking someone else's notification read affects nothing
    assert world.api("stu_b").post("/notifications/read", {"ids": [nid]}).json()["marked"] == 0
