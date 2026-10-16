"""Role → permission mapping (SECURITY_MODEL §5.1).

Only permissions used by existing routes are defined; later phases add their own.
"""

from enum import StrEnum
from typing import Literal

Role = Literal["owner", "analyst", "viewer"]


class Permission(StrEnum):
    BUSINESS_READ = "business:read"
    BUSINESS_UPDATE = "business:update"
    DATASET_READ = "dataset:read"
    DATASET_UPLOAD = "dataset:upload"
    AUDIT_READ = "audit:read"
    ANALYTICS_READ = "analytics:read"
    ANALYSIS_RUN = "analysis:run"
    INVESTIGATION_READ = "investigation:read"
    INVESTIGATION_RUN = "investigation:run"
    HITL_ANSWER = "hitl:answer"
    PLAN_CREATE = "plan:create"
    PLAN_DECIDE = "plan:decide"  # reject, request changes, revise (AMD-17)
    PLAN_APPROVE = "plan:approve"  # owner only


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    "owner": frozenset(Permission),
    "analyst": frozenset(
        {
            Permission.BUSINESS_READ,
            Permission.DATASET_READ,
            Permission.DATASET_UPLOAD,
            Permission.ANALYTICS_READ,
            Permission.ANALYSIS_RUN,
            Permission.INVESTIGATION_READ,
            Permission.INVESTIGATION_RUN,
            Permission.HITL_ANSWER,
            Permission.PLAN_CREATE,
            Permission.PLAN_DECIDE,
        }
    ),
    "viewer": frozenset(
        {
            Permission.BUSINESS_READ,
            Permission.DATASET_READ,
            Permission.ANALYTICS_READ,
            Permission.INVESTIGATION_READ,
        }
    ),
}
