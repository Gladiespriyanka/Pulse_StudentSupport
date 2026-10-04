from sqlalchemy import select

from app.models import KnowledgeArticle, Request
from tests.test_workflow import submit


def mk_cluster(world, n=3, loc="Hostel B"):
    a, b, c = world.api("stu_a"), world.api("stu_b"), world.api("stu_c")
    ids = []
    for api, txt in ((a, f"The lift in {loc} is broken"), (b, f"Water leak near the lift at {loc}"), (c, f"{loc} wifi is down and the lift is stuck")):
        ids.append(submit(api, txt)["id"])
    return ids[:n]


def test_cluster_suggestion_is_rule_based_and_scoped(world):
    mk_cluster(world)
    # a different location is unrelated
    submit(world.api("stu_a"), "Library wifi is not working")
    cmd = world.api("command").get("/intelligence/incident-clusters").json()
    assert len(cmd) == 1
    c = cmd[0]
    assert c["location"]["name"] == "Hostel B" and c["request_count"] == 3
    assert "3+" in c["rule"] and "score" not in str(c).lower() and "confidence" not in str(c).lower()
    # warden sees the cluster built only from requests they may see
    assert world.api("warden").get("/intelligence/incident-clusters").json()[0]["request_count"] == 3
    # academic head sees nothing from hostel
    assert world.api("acad_head").get("/intelligence/incident-clusters").json() == []


def test_incident_problem_change_full_chain(world, db):
    ids = mk_cluster(world)
    head, cmd = world.api("hostel_head"), world.api("command")

    # 1. INCIDENT from clustered requests
    inc = head.post("/incidents", {"title": "Hostel B lift out of service", "request_ids": ids, "description": "Lift stuck on floor 2"})
    assert inc.status_code == 201, inc.text
    inc = inc.json()
    assert inc["linked_request_count"] == 3 and inc["department"]["name"] == "Hostel & Facilities"
    assert all(db.get(Request, i).incident_id for i in ids)
    # already-linked requests cannot be linked twice
    assert head.post("/incidents", {"title": "dup", "request_ids": ids[:1]}).status_code == 409
    # joint work order: tasks for several departments
    t = head.post("/tasks", {"parent_type": "incident", "parent_id": inc["id"], "title": "Inspect lift motor", "assignee_id": str(world.users.warden)})
    assert t.status_code == 201
    # cannot resolve while tasks are open
    r = head.patch(f"/incidents/{inc['id']}", {"status": "resolved", "note": "Fixed"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "open_tasks"

    # 2. PROBLEM from the incident
    prob = head.post(f"/incidents/{inc['id']}/problem", {"title": "Lift motor keeps failing"})
    assert prob.status_code == 201
    prob = prob.json()
    assert prob["incidents"][0]["ref"] == f"INC-{inc['number']}"
    assert head.post(f"/incidents/{inc['id']}/problem", {}).status_code == 409
    # known error needs cause + workaround
    assert head.patch(f"/problems/{prob['id']}", {"status": "investigating"}).status_code == 200
    assert head.patch(f"/problems/{prob['id']}", {"status": "known_error"}).status_code == 422
    assert head.patch(f"/problems/{prob['id']}", {"root_cause": "Worn motor bearings", "workaround": "Use Block A lift"}).status_code == 200
    assert head.patch(f"/problems/{prob['id']}", {"status": "known_error"}).status_code == 200

    # 3. CHANGE from the problem; medium risk -> needs plans -> Command approves
    ch = head.post("/changes", {"title": "Replace lift motor", "problem_id": prob["id"], "risk": "medium"}).json()
    assert ch["status"] == "draft"
    sub = head.post(f"/changes/{ch['id']}/submit")
    assert sub.status_code == 422 and sub.json()["error"]["code"] == "plans_required"
    head.patch(f"/changes/{ch['id']}", {"implementation_plan": "Vendor swaps motor Sat", "rollback_plan": "Reinstall old motor"})
    assert head.post(f"/changes/{ch['id']}/submit").json()["status"] == "awaiting_approval"
    # the requester (and other dept heads) cannot approve; command can
    assert head.get("/approvals").json() == []
    ap = cmd.get("/approvals").json()
    assert len(ap) == 1 and ap[0]["subject"]["ref"] == f"CHG-{ch['number']}"
    assert head.post(f"/changes/{ch['id']}/start").status_code == 409  # not approved yet
    assert cmd.post(f"/approvals/{ap[0]['id']}/decision", {"approve": True, "reason": "Safety critical"}).status_code == 200
    assert head.get(f"/changes/{ch['id']}").json()["status"] == "approved"
    assert head.post(f"/changes/{ch['id']}/start").json()["status"] == "implementing"
    done = head.post(f"/changes/{ch['id']}/complete", {"outcome": "successful", "note": "Motor replaced, tested"})
    assert done.json()["status"] == "completed"

    # problem resolved + knowledge draft created from it
    p2 = head.get(f"/problems/{prob['id']}").json()
    assert p2["status"] == "resolved" and p2["resolution"] == "Motor replaced, tested"
    art = db.scalar(select(KnowledgeArticle).where(KnowledgeArticle.source_problem_id == prob["id"]))
    assert art and art.status == "draft" and "Worn motor bearings" in art.body
    # incident can now be closed out, resolving the linked requests for students
    for tid in [x["id"] for x in head.get(f"/tasks?parent_type=incident&parent_id={inc['id']}").json()["items"]]:
        assert world.api("warden").patch(f"/tasks/{tid}", {"status": "done"}).status_code == 200
    res = head.patch(f"/incidents/{inc['id']}", {"status": "resolved", "note": "Lift repaired", "resolve_linked_requests": True})
    assert res.status_code == 200 and res.json()["status"] == "resolved"
    for i in ids:
        assert db.get(Request, i).status == "resolved"
    assert world.api("stu_a").get(f"/requests/{ids[0]}").json()["feedback_requested"] is True
    # publishing the draft makes it visible to staff
    assert head.patch(f"/knowledge/{art.id}", {"status": "published"}).status_code == 200


def test_low_risk_change_approved_by_department_head_but_never_by_requester(world):
    head, other = world.api("hostel_head"), world.api("fin_head")
    ch = head.post("/changes", {"title": "Repaint corridor", "risk": "low"}).json()
    assert head.post(f"/changes/{ch['id']}/submit").status_code == 200
    # requester is a dept head -> approval escalates to command (cannot self-approve)
    assert head.get("/approvals").json() == [] and other.get("/approvals").json() == []
    assert len(world.api("command").get("/approvals").json()) == 1


def test_low_risk_change_by_command_needs_no_foreign_head(world):
    w = world.api("warden")
    assert w.post("/changes", {"title": "x" * 5}).status_code == 403  # staff cannot raise changes
    cmd = world.api("command")
    ch = cmd.post("/changes", {"title": "Campus-wide wifi policy", "risk": "low", "department_id": str(world.dept["hostel"])}).json()
    cmd.post(f"/changes/{ch['id']}/submit")
    # approver is the department head of the owning department; command cannot approve its own request
    assert len(world.api("hostel_head").get("/approvals").json()) == 1


def test_failed_change_does_not_resolve_problem_and_cancel_flow(world):
    head = world.api("hostel_head")
    inc = head.post("/incidents", {"title": "Water outage"}).json()
    prob = head.post(f"/incidents/{inc['id']}/problem", {}).json()
    ch = head.post("/changes", {"title": "Replace pump", "problem_id": prob["id"], "risk": "low"}).json()
    head.post(f"/changes/{ch['id']}/submit")
    aid = world.api("command").get("/approvals").json()[0]["id"]
    world.api("command").post(f"/approvals/{aid}/decision", {"approve": True})
    head.post(f"/changes/{ch['id']}/start")
    head.post(f"/changes/{ch['id']}/complete", {"outcome": "failed", "note": "Pump incompatible"})
    assert head.get(f"/problems/{prob['id']}").json()["status"] == "open"
    ch2 = head.post("/changes", {"title": "Another", "risk": "low"}).json()
    assert head.post(f"/changes/{ch2['id']}/cancel").json()["status"] == "cancelled"
    assert head.post(f"/changes/{ch2['id']}/cancel").status_code == 409


def test_staff_cannot_manage_other_departments_incidents(world):
    inc = world.api("hostel_head").post("/incidents", {"title": "Hostel pipe burst"}).json()
    ah = world.api("acad_head")
    assert ah.patch(f"/incidents/{inc['id']}", {"status": "investigating"}).status_code == 403
    assert ah.post(f"/incidents/{inc['id']}/problem", {}).status_code == 403
    assert ah.get(f"/incidents/{inc['id']}").status_code == 200  # incidents are campus-visible operational data (no personal data)


def test_problem_suggestion_after_repeat_incidents_on_same_asset(world, db):
    from app.models import Asset

    asset = Asset(code="LIFT-B1", name="Hostel B lift", location_id=world.loc["hostel-b"], department_id=world.dept["hostel"])
    db.add(asset)
    db.commit()
    head = world.api("hostel_head")
    for i in range(2):
        head.post("/incidents", {"title": f"Lift fault {i}", "asset_id": str(asset.id)})
    s = world.api("command").get("/intelligence/problem-suggestions").json()
    assert len(s) == 1 and s[0]["incident_count"] == 2 and s[0]["basis"] == "asset"


# ---- reports (computed from real rows) ------------------------------------------------------------------------
def test_reports_reflect_real_data_and_scope(world, db):
    a = world.api("stu_a")
    w = world.api("hostel_head")
    r1 = submit(a)["id"]
    r2 = submit(a, "I am worried about my attendance")["id"]
    w.post(f"/requests/{r1}/assign", {"assignee_id": str(world.users.hostel_head)})
    w.post(f"/requests/{r1}/status", {"status": "in_progress"})
    w.post(f"/requests/{r1}/status", {"status": "resolved", "note": "Done"})
    a.post(f"/requests/{r1}/feedback", {"helpful": True})
    s = world.api("command").get("/reports/summary").json()
    assert s["scope"] == "campus" and s["requests"]["created"] == 2 and s["requests"]["resolved"] == 1
    assert s["feedback"] == {"responses": 1, "helpful": 1, "helpful_pct": 100.0}
    assert s["sla"]["resolution_compliance_pct"] == 100.0
    # department head only ever gets their own department, even when asking for another
    hs = w.get(f"/reports/summary?department_id={world.dept['academic']}").json()
    assert hs["scope"] == "department" and hs["requests"]["created"] == 1
    assert "care" not in hs and "itsm" not in hs
    assert "care" in s and "itsm" in s
    csv = world.api("command").get("/reports/summary.csv")
    assert csv.status_code == 200 and "Hostel & Facilities" in csv.text
    brief = world.api("command").get("/reports/briefing").json()
    assert "2 created" in brief["text"] and "Aggregates only" in brief["text"]


def test_heatmap_masks_small_sensitive_cells_but_not_operational_ones(world):
    a = world.api("stu_a")
    for _ in range(2):
        submit(a, "Water leak in hostel b")
    submit(a, "I feel stressed and lonely near the library", consent_share=True)
    h = world.api("command").get("/reports/heatmap").json()
    cells = {(c["location"], c["category"]): c for c in h["cells"]}
    assert cells[("Hostel B", "hostel")]["count"] == 2 and not cells[("Hostel B", "hostel")]["masked"]
    w = cells[("Library", "wellbeing")]
    assert w["masked"] is True and w["count"] is None and h["masked_cells"] == 1
