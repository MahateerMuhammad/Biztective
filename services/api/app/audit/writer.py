"""Append-only audit writer (SEC-18).

Callers commit in the same transaction as the audited change.
"""

import ipaddress
from typing import Any
from uuid import UUID

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import request_id_var
from app.models import AuditLog


def ip_prefix(host: str | None) -> str | None:
    """Truncate to /24 (IPv4) or /48 (IPv6) so audit records do not hold full addresses."""
    if host is None:
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    prefix = 24 if address.version == 4 else 48
    return str(ipaddress.ip_network(f"{address}/{prefix}", strict=False))


async def record_audit(
    session: AsyncSession,
    *,
    action: str,
    actor_user_id: UUID | None,
    business_id: UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    client_host: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    # Core insert without RETURNING: rows with business_id NULL are insertable but not selectable.
    await session.execute(
        insert(AuditLog).values(
            business_id=business_id,
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            request_id=request_id_var.get(),
            ip_prefix=ip_prefix(client_host),
            metadata_=metadata or {},
        )
    )
