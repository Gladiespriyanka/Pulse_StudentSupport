"""Tasks: units of work under a request, incident, problem or change (case interventions live in cases.py)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import Conflict, Forbidden, Invalid, NotFound
from app.models import Case, Change, Feedback, Incident, Problem, Request, Task, User
from app.models.base import utcnow
from app.models.enums import TaskOutcome, TaskStatus
from app.rbac import ASSIGNABLE_ROLES
from app.security.deps import Principal
from app.services import access, notify as N, requests as R
from app.services.audit import audit
from app.services.redact import redact_pii

PARENTS = {"request": Request, "incident": Incident, "problem": Problem, "change": Change}
NEXT = {
    "todo": {"in_progress", "blocked", "done", "cancelled"},
    "in_progress": {"blocked", "done", "cancelled", "todo"},
    "blocked": {"in_progress", "cancelled", "todo"},
    "done": {"in_progress"},
    "cancelled": set(),
}


def _parent(db: Session, p: Principal, ptype: str, pid: uuid.UUID):
    model = PARENTS.get(ptype)
    if not model:
        raise Invalid("Unknown parent type.")
    if ptype == "request":
        req = R.get_request(db, p, pid)
        if not R.can_manage(db, p, req):
            raise Forbidden("You cannot add tasks to this request.")
        return req, req.department_id
    if not p.can(f"{ptype}.manage"):
        raise Forbidden(f"You cannot add tasks to a {ptype}.")
    obj = db.get(model, pid)
    if not obj:
        raise NotFound(f"{ptype.title()} not found")
    return obj, obj.department_id


def create(db: Session, p: Principal, ptype: str, pid: uuid.UUID, title: str, assignee_id: uuid.UUID | None,
           due_at: datetime | None, description: str | None, priority: str = "medium") -> Task:
    parent, dept = _parent(db, p, ptype, pid)
    assignee = None
    if assignee_id:
        assignee = db.get(User, assignee_id)
        if not assignee or not assignee.is_active or assignee.role.code not in ASSIGNABLE_ROLES or not assignee.staff:
            raise Invalid("Choose an active staff member.", code="invalid_assignee")
        if ptype == "request" and assignee.staff.department_id != dept:
            raise Invalid("For request tasks the assignee must be in the request's department.", code="invalid_assignee")
    task = Task(title=title.strip()[:200], description=redact_pii(description)[0] if description else None,
                department_id=assignee.staff.department_id if assignee else dept, assignee_id=assignee_id, assignee=assignee,
                created_by=p.id, due_at=due_at, priority=priority, **{f"{ptype}_id": pid})
    db.add(task)
    db.flush()
    if assignee_id and assignee_id != p.id:
        N.notify(db, [assignee_id], "task.assigned", f"New task: {task.title[:80]}", entity_type="task", entity_id=task.id)
    audit(db, "task.created", actor=p, entity_type="task", entity_id=task.id,
          subject=parent.requester_id if ptype == "request" else None, details={"parent": ptype, "parent_id": str(pid)})
    return task


def _can_edit(p: Principal, t: Task) -> bool:
    if t.assignee_id == p.id or t.created_by == p.id:
        return True
    if t.case_id:
        return False
    return p.can("task.manage") and (p.role == "command" or (p.department_id is not None and p.department_id == t.department_id))


def update(db: Session, p: Principal, t: Task, *, status: str | None = None, outcome: str | None = None,
           outcome_note: str | None = None, assignee_id: uuid.UUID | None = None, due_at: datetime | None = None,
           set_assignee: bool = False, now: datetime | None = None) -> Task:
    now = now or utcnow()
    if not _can_edit(p, t):
        raise Forbidden("You cannot edit this task.")
    if status and status != t.status:
        if status not in NEXT.get(t.status, set()):
            raise Conflict(f"A task that is '{t.status}' cannot move to '{status}'.", code="invalid_transition")
        if status == TaskStatus.DONE.value:
            if t.case_id and outcome not in {o.value for o in TaskOutcome}:
                raise Invalid("Record the outcome (helped / partly / no change) before completing an intervention.", code="outcome_required")
            t.completed_at = now
        else:
            t.completed_at = None
            if t.status == TaskStatus.DONE.value:
                t.outcome = t.outcome_note = None
        t.status = status
    if outcome:
        if outcome not in {o.value for o in TaskOutcome}:
            raise Invalid("Invalid outcome.")
        t.outcome = outcome
    if outcome_note is not None:
        t.outcome_note = redact_pii(outcome_note)[0] or None
    if due_at is not None:
        t.due_at = due_at
    if set_assignee:
        if assignee_id:
            a = db.get(User, assignee_id)
            if not a or not a.is_active or a.role.code not in ASSIGNABLE_ROLES | {"mentor"}:
                raise Invalid("Choose an active staff member.", code="invalid_assignee")
            if t.case_id:
                from app.models import CaseParticipant

                part = db.get(CaseParticipant, (t.case_id, assignee_id))
                if not part or part.removed_at:
                    raise Invalid("Assign the task to someone on the case.", code="not_participant")
            t.assignee_id, t.assignee = assignee_id, a
            N.notify(db, [assignee_id], "task.assigned", f"Task assigned to you: {t.title[:80]}", entity_type="task", entity_id=t.id, exclude=[p.id])
        else:
            t.assignee_id, t.assignee = None, None
    case_student = db.scalar(select(Case.student_id).where(Case.id == t.case_id)) if t.case_id else None
    if t.case_id and status == TaskStatus.DONE.value:
        N.notify(db, [case_student], "task.done", "A support action was completed. You can tell us if it helped.",
                 entity_type="task", entity_id=t.id)
    audit(db, "task.updated", actor=p, entity_type="task", entity_id=t.id, subject=case_student,
          details={"status": t.status, "outcome": t.outcome})
    return t


def task_feedback(db: Session, p: Principal, t: Task, helpful: bool, comment: str | None) -> Feedback:
    case = db.get(Case, t.case_id) if t.case_id else None
    if not case or case.student_id != p.id:
        raise Forbidden("Only the student of this case can give feedback on its actions.")
    if t.status != TaskStatus.DONE.value:
        raise Conflict("You can give feedback once the action is done.", code="invalid_state")
    if db.scalar(select(func.count()).select_from(Feedback).where(Feedback.task_id == t.id, Feedback.student_id == p.id)):
        raise Conflict("You already gave feedback on this action.", code="already_given")
    fb = Feedback(student_id=p.id, task_id=t.id, helpful=helpful, comment=redact_pii(comment)[0] if comment else None)
    db.add(fb)
    if not helpful:
        t.status, t.completed_at, t.outcome = TaskStatus.TODO.value, None, None
        N.notify(db, [t.assignee_id, case.owner_id], "task.reopened", "The student said an action did not help - please revisit.",
                 entity_type="task", entity_id=t.id)
    audit(db, "task.feedback", actor=p, entity_type="task", entity_id=t.id, subject=p.id, details={"helpful": helpful})
    return fb
