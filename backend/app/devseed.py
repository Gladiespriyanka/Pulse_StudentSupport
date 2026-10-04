"""DEVELOPMENT / DEMO ONLY seed data.

Refuses to run unless APP_ENV is development/test AND the database name contains dev, demo or test.
It goes through the same service layer as the API, so seeded data obeys every workflow rule.
Production databases are never seeded: use `bootstrap-admin` and `load-starter-catalog` instead.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from sqlalchemy import select, text

from app.config import get_settings
from app.db import SessionLocal, get_engine
from app.models import Approval, Asset, Department, KnowledgeArticle, Location, Request, RequestEvent, Role, Service, User
from app.models.base import utcnow
from app.security.deps import Principal
from app.services import approvals as AP, cases as C, itsm as X, requests as R, sla, tasks as T, users as U
from app.starter import load_starter_catalog

KEEP = {"alembic_version", "roles", "permissions", "role_permissions"}

STAFF = [  # key, email, name, role, department
    ("academic", "academic@campus.edu", "Dr. Rao (Academic Head)", "dept_head", "academic"),
    ("warden", "warden@campus.edu", "Mr. Iyer (Hostel Warden)", "staff", "hostel"),
    ("warden2", "warden2@campus.edu", "Ms. Fernandes (Asst. Warden)", "staff", "hostel"),
    ("hostel_head", "hostel-head@campus.edu", "Dr. Menon (Hostel Chief Warden)", "dept_head", "hostel"),
    ("hospital", "hospital@campus.edu", "Dr. Shah (Medical Officer)", "staff", "medical"),
    ("counsellor", "counsellor@campus.edu", "Ms. Kapoor (Counsellor)", "counsellor", "wellbeing"),
    ("safety", "safety@campus.edu", "Safety Desk", "staff", "safety"),
    ("finance", "finance@campus.edu", "Ms. D'Souza (Finance Head)", "dept_head", "finance"),
    ("desk", "desk@campus.edu", "Service Desk Agent", "staff", "desk"),
    ("mentor", "mentor@campus.edu", "Prof. Nair (Faculty Mentor)", "mentor", "academic"),
    ("command", "command@campus.edu", "Campus Command", "command", None),
    ("admin", "super@campus.edu", "System Administrator", "admin", None),
]
STUDENT_NAMES = ["Aarav Sharma", "Diya Patel", "Rohan Gupta", "Ananya Reddy", "Kabir Singh", "Meera Nair", "Ishaan Verma", "Saanvi Joshi",
                 "Vihaan Das", "Tara Menon", "Arjun Kulkarni", "Naina Bose", "Dev Malhotra", "Riya Iyer", "Yash Chopra", "Kiara Shah",
                 "Aditya Rao", "Zoya Khan", "Neil D'Souza", "Pooja Mishra", "Samar Ali", "Isha Banerjee", "Kunal Jain", "Myra Thomas", "Om Prakash"]


def assert_safe_target() -> None:
    s = get_settings()
    dbname = s.database_url.lower().rsplit("/", 1)[-1].split("?")[0]
    if s.is_production:
        raise SystemExit("REFUSED: seed-dev must never run with APP_ENV=production.")
    if not any(t in dbname for t in ("dev", "demo", "test")):
        raise SystemExit(f"REFUSED: database '{dbname}' does not look like a dev/demo/test database (name must contain dev, demo or test).")


def _principal(u: User) -> Principal:
    return Principal(user=u, role=u.role.code, perms=frozenset(p.code for p in u.role.permissions),
                     department_id=u.staff.department_id if u.staff else None)


def wipe() -> None:
    eng = get_engine()
    with eng.begin() as c:
        names = [r[0] for r in c.execute(text("select tablename from pg_tables where schemaname='public'")) if r[0] not in KEEP]
        c.execute(text("ALTER TABLE audit_logs DISABLE TRIGGER USER"))  # allowed only because this is a dev/demo database
        c.execute(text("TRUNCATE " + ",".join(names) + " RESTART IDENTITY CASCADE"))
        c.execute(text("ALTER TABLE audit_logs ENABLE TRIGGER USER"))
        for prio, a, b in (("critical", 15, 240), ("high", 120, 1440), ("medium", 480, 4320), ("low", 1440, 10080)):
            c.execute(text("INSERT INTO sla_policies (id, service_id, priority, response_minutes, resolution_minutes, at_risk_pct, severe_multiplier, is_active) "
                           "VALUES (gen_random_uuid(), NULL, :p, :a, :b, 75, 2.0, true)"), {"p": prio, "a": a, "b": b})


def seed(reset: bool = False) -> dict:
    assert_safe_target()
    s = get_settings()
    if reset:
        wipe()
    db = SessionLocal()
    now = utcnow()
    rnd = random.Random(7)
    try:
        if not db.scalar(select(Role.id).limit(1)):
            raise SystemExit("Run `alembic upgrade head` first.")
        if db.scalar(select(User.id).limit(1)):
            raise SystemExit("Database already has users. Use --reset to wipe a dev/demo database.")
        load_starter_catalog(db)
        D = {d.code: d for d in db.scalars(select(Department))}
        L = {l.code: l for l in db.scalars(select(Location))}
        SV = {x.code: x for x in db.scalars(select(Service))}
        pw = s.demo_password

        users: dict[str, User] = {}
        for key, email, name, role, dept in STAFF:
            users[key], _ = U.create_user(db, None, email=email, full_name=name, role_code=role, password=pw,
                                          department_id=D[dept].id if dept else None)
        students: list[User] = []
        for i, name in enumerate(STUDENT_NAMES):
            email = "aarav@campus.edu" if i == 0 else f"student{i + 1}@campus.edu"
            u, _ = U.create_user(db, None, email=email, full_name=name, role_code="student", password=pw, roll_no=f"CS24{i + 1:03d}",
                                 programme="B.Tech CSE", year=1 + i % 3, mentor_id=users["mentor"].id if i < 12 else None,
                                 residence_location_id=L["hostel-b" if i % 2 else "hostel-a"].id, academic_department_id=D["academic"].id)
            students.append(u)
        db.flush()
        P = {k: _principal(u) for k, u in users.items()}
        SP = [_principal(u) for u in students]

        for code, name, typ, loc, dept in [("LIFT-B1", "Hostel B lift", "lift", "hostel-b", "hostel"), ("AC-LIB1", "Library air conditioning", "hvac", "library", "hostel"),
                                           ("PUR-MESS1", "Mess water purifier", "purifier", "mess", "hostel"), ("PROJ-C1", "Block C projector", "projector", "block-c", "academic")]:
            db.add(Asset(code=code, name=name, asset_type=typ, location_id=L[loc].id, department_id=D[dept].id))
        db.flush()

        def make(si: int, text_: str, days_ago: float, service: str | None = None, consent: bool | None = None) -> Request:
            when = now - timedelta(days=days_ago)
            req, _ = R.create_request(db, SP[si], R.NewRequest(text_, SV[service].id if service else None, None, consent), now=when)
            req.created_at = when
            return req

        def retime(req: Request, start: datetime, end: datetime) -> None:
            evs = db.scalars(select(RequestEvent).where(RequestEvent.request_id == req.id).order_by(RequestEvent.created_at, RequestEvent.id)).all()
            for i, ev in enumerate(evs):
                ev.created_at = start + (end - start) * i / max(1, len(evs) - 1)

        def work(req: Request, actor: str, hours_to_ack: float, hours_to_resolve: float, helpful: bool | None, note: str = "Sorted out") -> None:
            start = req.created_at
            owner = _principal(req.assignee) if req.assignee else P[actor]
            R.transition(db, owner, req, "acknowledged", now=start + timedelta(hours=hours_to_ack))
            req.first_response_at = start + timedelta(hours=hours_to_ack)
            R.transition(db, owner, req, "in_progress", now=start + timedelta(hours=hours_to_ack + 1))
            end = start + timedelta(hours=hours_to_resolve)
            R.transition(db, owner, req, "resolved", note=note, now=end)
            req.resolved_at = end
            if helpful is not None:
                R.give_feedback(db, SP[students.index(req.requester)], req, helpful, None, now=end + timedelta(hours=3))
            retime(req, start, end + timedelta(hours=3))

        # ---- history for reports: resolved requests with real timings and feedback
        samples = [("Water leak in my hostel room bathroom", "hostel-maintenance", "hostel_head"), ("The fan in my room stopped working", None, "hostel_head"),
                   ("Mess food was undercooked yesterday", "mess-feedback", "hostel_head"), ("I need help with my assignment deadline", None, "academic"),
                   ("Fee receipt for last semester is missing", "fee-query", "finance"), ("Wifi is very slow in hostel a", None, "hostel_head"),
                   ("I have a fever and sore throat", "medical-help", "hospital")]
        for n in range(26):
            txt, svc, actor = samples[n % len(samples)]
            consent = True if svc in ("medical-help",) else None
            r = make(rnd.randrange(1, 25), txt, rnd.uniform(8, 28), svc, consent)
            work(r, actor if actor in P else "hostel_head", rnd.uniform(0.5, 9), rnd.uniform(6, 90), rnd.random() < 0.93)
        # ---- Aarav's story: three areas in three weeks (rule-based case suggestion), consent given
        r1 = make(0, "My roommate is very noisy at night in hostel b", 15)
        work(r1, "hostel_head", 2, 30, True, "Roommate counselled, room swap offered")
        make(0, "I am worried about my attendance, I missed many classes", 9)
        make(0, "I feel really overwhelmed and stressed lately", 2, consent=True)
        C.set_consents(db, SP[0], True, [D["wellbeing"].id, D["hostel"].id, D["academic"].id])
        # ---- Hostel B cluster -> incident (joint work order)
        c1 = [make(2, "The lift in Hostel B is stuck again", 3), make(3, "Water leak near the lift at Hostel B", 2), make(4, "Hostel B wifi is down and the lift is stuck", 1)]
        inc = X.create_incident(db, P["hostel_head"], title="Hostel B lift out of service", description="Lift stuck repeatedly; leak near the shaft.",
                                department_id=D["hostel"].id, location_id=L["hostel-b"].id, asset_id=db.scalar(select(Asset.id).where(Asset.code == "LIFT-B1")),
                                priority=None, request_ids=[r.id for r in c1])
        T.create(db, P["hostel_head"], "incident", inc.id, "Inspect lift motor and shaft leak", users["warden"].id, now + timedelta(days=1), None, "high")
        # ---- a second cluster that nobody has turned into an incident yet (shows the suggestion)
        make(8, "Projector in Block C lab is not working", 2)
        make(9, "Block C lab wifi keeps dropping", 1.5)
        make(10, "Block C classroom is very dark and the light is broken", 1)
        # ---- a delayed request that breaches its SLA (sweep will escalate it)
        late = make(5, "Hot water has not been available in my wing for days", 6.5)
        # ---- approval waiting
        make(6, "I need a fee waiver, my family income has dropped", 1, "fee-waiver")
        # ---- anonymous peer concern
        R.create_peer_concern(db, SP[7], "A friend of mine has stopped coming to class and seems very low.", None, now - timedelta(hours=20))
        # ---- ITSM history: resolved problem with published knowledge
        p_inc = X.create_incident(db, P["hostel_head"], title="Mess purifier failing repeatedly", description="Third purifier fault this month",
                                  department_id=D["hostel"].id, location_id=L["mess"].id, asset_id=db.scalar(select(Asset.id).where(Asset.code == "PUR-MESS1")),
                                  priority="medium", request_ids=[])
        prob = X.link_problem(db, P["hostel_head"], p_inc, problem_id=None, title="Purifier filter cartridges wear out early")
        X.update_problem(db, P["hostel_head"], prob, status="investigating", root_cause="Hard water shortens cartridge life", workaround="Boil/serve bottled water",
                         resolution=None, owner_id=None, set_owner=False)
        ch = X.create_change(db, P["hostel_head"], title="Install a sediment pre-filter", description=None, problem_id=prob.id, department_id=D["hostel"].id,
                             risk="medium", implementation_plan="Fit pre-filter on inlet line", rollback_plan="Bypass the pre-filter", planned_start=None, planned_end=None)
        X.submit_change(db, P["hostel_head"], ch)
        AP.decide(db, P["command"], db.scalars(select(Approval).where(Approval.change_id == ch.id)).first(), True, "Approved")
        X.start_change(db, P["hostel_head"], ch)
        X.complete_change(db, P["hostel_head"], ch, "successful", "Pre-filter fitted, no faults since")
        art = db.scalar(select(KnowledgeArticle).where(KnowledgeArticle.source_problem_id == prob.id))
        art.status, art.audience, art.published_at = "published", "staff", now
        # ---- a medium-risk change waiting for Command approval
        ch2 = X.create_change(db, P["hostel_head"], title="Replace Hostel B lift motor", description=None, problem_id=None, department_id=D["hostel"].id,
                              risk="medium", implementation_plan="Vendor replaces motor on a Saturday", rollback_plan="Reinstall old motor", planned_start=None, planned_end=None)
        X.submit_change(db, P["hostel_head"], ch2)
        # ---- public knowledge base (students)
        for title, body, dept in [
            ("How do I report a maintenance problem?", "Describe the problem in the PULSE request box. It is routed to Hostel & Facilities automatically and you can follow its progress.", "hostel"),
            ("Wi-Fi is slow or not connecting", "Forget the network and reconnect. If it fails in your hostel wing, raise a request with your hostel and room.", "hostel"),
            ("What to expect from counselling", "Sessions are voluntary and confidential. You choose who may see your request in Privacy & consent.", "wellbeing"),
            ("How does a fee waiver request work?", "Submit a fee waiver request. The Finance head reviews it; you are told the decision and the reason.", "finance")]:
            db.add(KnowledgeArticle(title=title, body=body, department_id=D[dept].id, audience="public", status="published", published_at=now,
                                    created_by=users["admin"].id, updated_by=users["admin"].id))
        db.flush()
        stats = sla.sweep(db, now)
        db.commit()
        return {"users": len(STAFF) + len(students), "requests": db.query(Request).count(), "sweep": stats}
    finally:
        db.close()
