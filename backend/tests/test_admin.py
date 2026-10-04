import io

from sqlalchemy import select, text

from app.models import AuditLog, Department, User
from tests.conftest import PW, Api
from tests.test_workflow import submit


def csv_file(content: str, name="x.csv"):
    return {"file": (name, io.BytesIO(content.encode()), "text/csv")}


def test_department_crud_and_single_triage_desk(world):
    ad = world.api("admin")
    r = ad.post("/admin/departments", {"code": "transport", "name": "Transport", "is_triage_desk": True})
    assert r.status_code == 201 and r.json()["is_triage_desk"] is True
    desks = [d for d in ad.get("/admin/departments").json() if d["is_triage_desk"]]
    assert [d["code"] for d in desks] == ["transport"]  # the previous desk was demoted atomically
    assert ad.post("/admin/departments", {"code": "transport", "name": "Dup"}).status_code == 409
    assert ad.post("/admin/departments", {"code": "Bad Code!", "name": "Bad"}).status_code == 422
    did = r.json()["id"]
    assert ad.patch(f"/admin/departments/{did}", {"name": "Transport Office", "is_active": False}).json()["is_active"] is False
    assert ad.patch("/admin/departments/00000000-0000-0000-0000-000000000000", {"name": "Nope"}).status_code == 404
    # inactive departments disappear from the everyday catalogue
    assert "transport" not in [d["code"] for d in world.api("stu_a").get("/catalog/departments").json()]


def test_service_and_triage_rule_changes_take_effect_immediately(world):
    ad = world.api("admin")
    svc = ad.post("/admin/services", {"code": "bus-pass", "name": "Bus pass", "department_id": str(world.dept["desk"]), "category": "transport"})
    assert svc.status_code == 201
    sid = svc.json()["id"]
    assert ad.post("/admin/services", {"code": "x-y", "name": "Broken", "department_id": "00000000-0000-0000-0000-000000000000"}).status_code in (409, 422)
    a = world.api("stu_a")
    assert a.post("/triage/preview", {"description": "I need a bus pass"}).json()["service"] is None
    assert ad.post("/admin/triage-rules", {"service_id": sid, "keyword": "Bus Pass", "weight": 5}).json()["keyword"] == "bus pass"
    assert a.post("/triage/preview", {"description": "I need a bus pass"}).json()["service"] == "bus-pass"
    assert a.post("/admin/triage-rules", {"service_id": sid, "keyword": "x"}).status_code == 403
    # deactivating a service removes it from the student catalogue and from routing
    ad.patch(f"/admin/services/{sid}", {"is_active": False})
    assert "Bus pass" not in [s["name"] for s in a.get("/catalog/services").json()]
    assert a.post("/triage/preview", {"description": "I need a bus pass"}).json()["service"] is None
    # duplicate rule rejected cleanly
    r1 = ad.post("/admin/triage-rules", {"service_id": sid, "keyword": "pass here"})
    assert r1.status_code == 201 and ad.post("/admin/triage-rules", {"service_id": sid, "keyword": "pass here"}).status_code == 409
    assert ad.delete(f"/admin/triage-rules/{r1.json()['id']}").status_code == 204


def test_sla_configuration_applies_to_new_requests(world):
    ad = world.api("admin")
    pols = ad.get("/admin/sla-policies").json()
    default_med = next(p for p in pols if p["priority"] == "medium" and p["service_id"] is None)
    assert ad.patch(f"/admin/sla-policies/{default_med['id']}", {"resolution_minutes": 60, "response_minutes": 10}).status_code == 200
    from datetime import datetime
    r = submit(world.api("stu_a"))
    d = datetime.fromisoformat(r["sla"]["resolution_due_at"]) - datetime.fromisoformat(r["created_at"])
    assert 59 <= d.total_seconds() / 60 <= 61
    assert ad.patch(f"/admin/sla-policies/{default_med['id']}", {"at_risk_pct": 150}).status_code == 422


def test_user_management_roles_and_guards(world):
    ad = world.api("admin")
    body = {"email": "w3@campus.edu", "full_name": "New Warden", "role": "staff", "department_id": str(world.dept["hostel"]), "password": "Strong#Pass2026!"}
    assert ad.post("/admin/users", body).status_code == 201
    assert ad.post("/admin/users", body).status_code == 409
    assert ad.post("/admin/users", {**body, "email": "w4@campus.edu", "department_id": None}).status_code == 422  # staff need a department
    assert ad.post("/admin/users", {**body, "email": "w5@campus.edu", "password": "weak"}).status_code == 422
    assert ad.post("/admin/users", {**body, "email": "s9@campus.edu", "role": "student"}).status_code == 422  # roll no required
    assert ad.post("/admin/users", {**body, "email": "x@campus.edu", "role": "wizard"}).status_code == 422
    assert Api(world.app).login("w3@campus.edu", "Strong#Pass2026!").status_code == 200
    # listing / search / filters
    lst = ad.get("/admin/users?q=warden&role=staff").json()
    assert lst["total"] >= 3 and all(u["role"] == "staff" for u in lst["items"])
    # cannot lock yourself or the last admin out
    me = world.users.admin
    assert ad.patch(f"/admin/users/{me}", {"is_active": False}).status_code == 409
    # role change takes effect immediately and invalidates the old token
    w = world.api("warden")
    assert w.get("/incidents").status_code == 200
    ad.patch(f"/admin/users/{world.users.warden}", {"role": "mentor"})
    assert w.get("/incidents").status_code == 401  # token_version bumped -> must sign in again
    # student <-> staff conversions are refused
    assert ad.patch(f"/admin/users/{world.users.stu_a}", {"role": "staff"}).status_code == 422
    # password reset issues a one-time temp password
    pw = ad.post(f"/admin/users/{world.users.stu_b}/reset-password").json()["temporary_password"]
    s = Api(world.app)
    assert s.login(world.email("stu_b"), PW).status_code == 401 and s.login(world.email("stu_b"), pw).status_code == 200


def test_permission_changes_are_enforced_live_and_guarded(world):
    ad = world.api("admin")
    roles = {r["code"]: r for r in ad.get("/admin/roles").json()["roles"]}
    assert "request.manage" in roles["staff"]["permissions"] and roles["staff"]["user_count"] >= 3
    w = world.api("warden")
    rid = submit(world.api("stu_a"))["id"]
    assert w.post(f"/requests/{rid}/status", {"status": "acknowledged"}).status_code == 200
    perms = [p for p in roles["staff"]["permissions"] if p != "request.manage"]
    assert ad.put("/admin/roles/staff/permissions", {"permissions": perms}).status_code == 200
    assert w.post(f"/requests/{rid}/status", {"status": "in_progress"}).status_code in (403, 404)  # revoked without re-login
    # guard rails
    assert ad.put("/admin/roles/admin/permissions", {"permissions": ["audit.read"]}).status_code == 409
    assert ad.put("/admin/roles/staff/permissions", {"permissions": ["admin.permissions"]}).status_code == 409
    assert ad.put("/admin/roles/student/permissions", {"permissions": ["request.create", "audit.read"]}).status_code == 409
    assert ad.put("/admin/roles/staff/permissions", {"permissions": ["no.such.perm"]}).status_code == 422
    assert ad.put("/admin/roles/ghost/permissions", {"permissions": []}).status_code == 404
    assert world.api("hostel_head").get("/admin/roles").status_code == 403
    assert world.db.scalar(select(AuditLog).where(AuditLog.action == "role.permissions_changed"))


def test_audit_log_query_filters_export_and_immutability(world, db):
    ad = world.api("admin")
    submit(world.api("stu_a"))
    ad.post("/admin/departments", {"code": "x1", "name": "X One"})
    rows = ad.get("/admin/audit-logs?action=request.").json()
    assert rows["total"] >= 1 and all(i["action"].startswith("request.") for i in rows["items"])
    by_actor = ad.get(f"/admin/audit-logs?actor_id={world.users.admin}").json()["items"]
    assert by_actor and all(i["actor"]["id"] == str(world.users.admin) for i in by_actor)
    assert ad.get("/admin/audit-logs?outcome=denied").json()["total"] == 0
    csv = ad.get("/admin/audit-logs/export.csv")
    assert csv.status_code == 200 and csv.text.startswith("id,timestamp,actor") and "department.created" in csv.text
    # no API exists to change or delete audit rows, and the database refuses too
    assert ad.delete("/admin/audit-logs/1").status_code in (404, 405)
    import pytest
    from sqlalchemy.exc import DBAPIError
    with pytest.raises(DBAPIError):
        db.execute(text("update audit_logs set action='x'"))
    db.rollback()
    with pytest.raises(DBAPIError):
        db.execute(text("delete from audit_logs"))
    db.rollback()


# ---- CSV import ------------------------------------------------------------------------------------------------------
def test_import_dry_run_changes_nothing_then_apply_persists(world, db):
    ad = world.api("admin")
    data = "code,name,description\nlegal,Legal Cell,Legal help\nlib,Library Services,\n"
    dry = ad.post("/admin/imports/departments", data={"dry_run": "true"}, files=csv_file(data)).json()
    assert dry["dry_run"] and not dry["applied"] and dry["created"] == 2 and dry["error_count"] == 0
    assert db.scalar(select(Department).where(Department.code == "legal")) is None
    done = ad.post("/admin/imports/departments", data={"dry_run": "false"}, files=csv_file(data)).json()
    assert done["applied"] and done["created"] == 2
    db.expire_all()
    assert db.scalar(select(Department).where(Department.code == "legal")).name == "Legal Cell"
    # re-import updates instead of duplicating
    again = ad.post("/admin/imports/departments", data={"dry_run": "false"}, files=csv_file("code,name\nlegal,Legal Aid Cell\n")).json()
    assert again["updated"] == 1 and again["created"] == 0
    assert any(a.action == "import.applied" for a in db.scalars(select(AuditLog)))


def test_import_is_all_or_nothing_and_reports_row_errors(world, db):
    ad = world.api("admin")
    bad = "code,name,department_code,category\ns1,Good one,hostel,hostel\ns2,Bad dept,nowhere,hostel\ns3,Bad category,hostel,spaceship\n,Missing code,hostel,hostel\n"
    r = ad.post("/admin/imports/services", data={"dry_run": "false"}, files=csv_file(bad)).json()
    assert not r["applied"] and r["error_count"] == 3
    assert {e["row"] for e in r["errors"]} == {3, 4, 5}
    from app.models import Service
    db.expire_all()
    assert db.scalar(select(Service).where(Service.code == "s1")) is None  # nothing was saved


def test_import_rejects_bad_files_and_formula_injection(world):
    ad = world.api("admin")
    assert ad.post("/admin/imports/departments", files=csv_file("code,wrong\nx,y\n")).status_code == 422
    assert ad.post("/admin/imports/nonsense", files=csv_file("a\n1\n")).status_code == 422
    r = ad.post("/admin/imports/departments", data={"dry_run": "true"}, files=csv_file("code,name\nx1,=HYPERLINK(\"http://evil\")\n")).json()
    assert r["error_count"] == 1 and "formula" in r["errors"][0]["message"]
    big = "code,name\n" + "\n".join(f"c{i},N{i}" for i in range(6000)) + "\n"
    assert ad.post("/admin/imports/departments", files=csv_file(big)).status_code == 422
    assert world.api("stu_a").post("/admin/imports/departments", files=csv_file("code,name\nx,y\n")).status_code == 403


def test_user_import_creates_students_staff_with_one_time_passwords(world):
    ad = world.api("admin")
    data = ("email,full_name,role,department_code,roll_no,programme,year,mentor_email,residence_location_code\n"
            "n1@campus.edu,New One,student,,R100,BTech,2,mentor@campus.edu,hostel-b\n"
            "n2@campus.edu,New Two,staff,hostel,,,,,\n"
            "n3@campus.edu,Bad Row,student,,,,,,\n")
    bad = ad.post("/admin/imports/users", data={"dry_run": "false"}, files=csv_file(data)).json()
    assert not bad["applied"] and bad["errors"][0]["row"] == 4 and bad["temporary_passwords"] == []
    ok = ad.post("/admin/imports/users", data={"dry_run": "false"}, files=csv_file("\n".join(data.splitlines()[:3]) + "\n")).json()
    assert ok["applied"] and ok["created"] == 2 and len(ok["temporary_passwords"]) == 2
    pw = ok["temporary_passwords"][0]["temporary_password"]
    s = Api(world.app)
    assert s.login("n1@campus.edu", pw).status_code == 200
    assert s.get("/requests").status_code == 403  # must change the temporary password first
    # the new student is wired to the mentor
    assert ad.get(f"/admin/users?q=n1@").json()["items"][0]["mentor_id"] == str(world.users.mentor)
    # passwords are not retrievable later
    assert "temporary_password" not in str(ad.get("/admin/users?q=n1@").json())


def test_asset_and_location_import_then_triage_attaches_the_asset(world):
    ad = world.api("admin")
    ad.post("/admin/imports/assets", data={"dry_run": "false"},
            files=csv_file("code,name,asset_type,location_code,department_code\nLIFT-B1,Hostel B lift,lift,hostel-b,hostel\n"))
    out = submit(world.api("stu_a"), "The lift in hostel B is making noises")
    assert out["asset"]["name"] == "Hostel B lift"
