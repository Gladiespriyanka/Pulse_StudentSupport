from enum import StrEnum


class Priority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


PRIORITY_ORDER = [Priority.LOW.value, Priority.MEDIUM.value, Priority.HIGH.value, Priority.CRITICAL.value]


class RequestStatus(StrEnum):
    NEW = "new"
    PENDING_APPROVAL = "pending_approval"
    ACKNOWLEDGED = "acknowledged"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


SLA_RUNNING = (RequestStatus.NEW.value, RequestStatus.ACKNOWLEDGED.value, RequestStatus.IN_PROGRESS.value)
OPEN_STATUSES = SLA_RUNNING + (RequestStatus.PENDING_APPROVAL.value,)


class RequestSource(StrEnum):
    PORTAL = "portal"  # chosen from the service catalog
    TRIAGE = "triage"  # free text routed by rules
    SOS = "sos"
    PEER = "peer"  # anonymous "worried about a friend"


class TaskStatus(StrEnum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskOutcome(StrEnum):
    HELPED = "helped"
    PARTLY = "partly"
    NO_CHANGE = "no_change"


class IncidentStatus(StrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    MITIGATED = "mitigated"
    RESOLVED = "resolved"
    CLOSED = "closed"


class ProblemStatus(StrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    KNOWN_ERROR = "known_error"
    RESOLVED = "resolved"
    CLOSED = "closed"


class ChangeStatus(StrEnum):
    DRAFT = "draft"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    IMPLEMENTING = "implementing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ChangeRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ChangeOutcome(StrEnum):
    SUCCESSFUL = "successful"
    FAILED = "failed"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class CaseStatus(StrEnum):
    SUGGESTED = "suggested"
    OPEN = "open"
    MONITORING = "monitoring"
    CLOSED = "closed"
    DISMISSED = "dismissed"


ACTIVE_CASE = (CaseStatus.SUGGESTED.value, CaseStatus.OPEN.value, CaseStatus.MONITORING.value)


class CaseOutcome(StrEnum):
    STABILISED = "stabilised"
    PARTLY = "partly"
    NO_CHANGE = "no_change"
    DECLINED = "declined"


class AccessStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    REVOKED = "revoked"


class KnowledgeStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class Audience(StrEnum):
    PUBLIC = "public"
    STAFF = "staff"


class LocationKind(StrEnum):
    HOSTEL = "hostel"
    ACADEMIC = "academic"
    LIBRARY = "library"
    MEDICAL = "medical"
    GATE = "gate"
    MESS = "mess"
    ADMIN = "admin"
    OTHER = "other"


class ServiceCategory(StrEnum):
    HOSTEL = "hostel"
    ACADEMIC = "academic"
    MEDICAL = "medical"
    WELLBEING = "wellbeing"
    SAFETY = "safety"
    FINANCE = "finance"
    IT = "it"
    TRANSPORT = "transport"
    OTHER = "other"


class AssetStatus(StrEnum):
    ACTIVE = "active"
    MAINTENANCE = "maintenance"
    RETIRED = "retired"


class ConsentKind(StrEnum):
    MENTOR = "mentor"
    DEPARTMENT = "department"


def values(enum_cls) -> list[str]:
    return [m.value for m in enum_cls]
