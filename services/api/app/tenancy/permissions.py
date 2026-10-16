"""Role → permission mapping (SECURITY_MODEL §5.1).

Only permissions used by existing routes are defined; later phases add their own.
"""

from enum import StrEnum
from typing import Literal

Role = Literal["owner", "analyst", "viewer"]


class Permission(StrEnum):
    BUSINESS_READ = "business:read"
    DATASET_READ = "dataset:read"
    AUDIT_READ = "audit:read"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    "owner": frozenset(Permission),
    "analyst": frozenset({Permission.BUSINESS_READ, Permission.DATASET_READ}),
    "viewer": frozenset({Permission.BUSINESS_READ, Permission.DATASET_READ}),
}
