"""What an approved plan version writes (AMD-18): a pure function of the approved, hashed
content and its approval. No LLM text, request field or client value reaches a cell; the
destination tabs and layouts are constants.
"""

import hashlib
import uuid
from typing import Any

from app.actions.sheets import sanitize
from app.analytics.evidence import sha256_of
from app.recovery.catalog import BY_CODE

KIND = "sheets.write_recovery_tasks"
ACTION_NAMESPACE = uuid.UUID("6f1d2c1e-5b8a-4f0e-9d3c-2a7b4e9c8f10")
MAX_ROWS = 5000
REVIEW_TAB = "ROOT Review Tasks"
QUEUE_TAB = "ROOT Service Queue"
LAYOUTS: dict[str, list[str]] = {
    REVIEW_TAB: [
        "task_key",
        "plan_id",
        "plan_version",
        "strategy",
        "title",
        "what_to_do",
        "scope",
        "estimated_net_mid",
        "currency",
        "approval_id",
        "approved_at",
        "status",
    ],
    QUEUE_TAB: [
        "queue_key",
        "plan_id",
        "plan_version",
        "strategy",
        "title",
        "customer_id",
        "instruction",
        "approval_id",
        "approved_at",
        "status",
    ],
}
QUEUE_INSTRUCTION = (
    "Internal follow-up only. ROOT has not contacted this customer; staff decide whether and "
    "how to contact them."
)
# Action types that become rows; every other approved type is listed as not executed.
EXECUTED = {"review_task": REVIEW_TAB, "customer_contact": QUEUE_TAB}


class NothingToExecuteError(Exception):
    pass


class TooManyRowsError(Exception):
    pass


def action_id(plan_id: uuid.UUID, version: int, content_hash: str) -> uuid.UUID:
    """Stable: the same approved version always names the same action."""
    return uuid.uuid5(ACTION_NAMESPACE, f"{plan_id}:{version}:{content_hash}:{KIND}")


def idempotency_key(action: uuid.UUID, payload_hash: str) -> str:
    return hashlib.sha256(f"{action}:{payload_hash}".encode()).hexdigest()


def payload_hash(payload: dict[str, Any]) -> str:
    return sha256_of(payload)


def column(n: int) -> str:
    """Spreadsheet column letter of the n-th column (1-based, n ≤ 26)."""
    return chr(ord("A") + n - 1)


def tab_range(title: str) -> str:
    return f"'{title}'!A:{column(len(LAYOUTS[title]))}"


def build(
    *,
    action: uuid.UUID,
    plan_id: uuid.UUID,
    version: int,
    content_hash: str,
    content: dict[str, Any],
    approval_id: uuid.UUID,
    approved_at: str,
) -> dict[str, Any]:
    selected = set(content["selected"])
    rows: dict[str, list[list[str]]] = {title: [] for title in LAYOUTS}
    not_executed: list[dict[str, str]] = []
    common = [str(plan_id), str(version)]
    approval = [str(approval_id), approved_at]
    for entry in sorted(content["strategies"], key=lambda s: int(s["rank"])):
        if entry["id"] not in selected:
            continue
        strategy = BY_CODE[entry["strategy"]]
        for action_type in entry["action_types"]:
            if action_type not in EXECUTED:
                not_executed.append(
                    {
                        "strategy": strategy.code,
                        "action_type": action_type,
                        "reason": "not_supported",
                    }
                )
        if "review_task" in entry["action_types"]:
            estimate = entry.get("estimate")
            net = estimate["scenarios"]["mid"]["net"] if estimate else ""
            rows[REVIEW_TAB].append(
                [
                    *common,
                    strategy.code,
                    strategy.title,
                    strategy.summary,
                    str(entry["incident"]["key"]),
                    str(net),
                    str(content["currency"]),
                    *approval,
                    "open",
                ]
            )
        if "customer_contact" in entry["action_types"]:
            for customer in entry.get("targeting", {}).get("selected", []):
                rows[QUEUE_TAB].append(
                    [
                        *common,
                        strategy.code,
                        strategy.title,
                        str(customer),
                        QUEUE_INSTRUCTION,
                        *approval,
                        "to_review",
                    ]
                )
    total = sum(len(r) for r in rows.values())
    if total == 0:
        raise NothingToExecuteError
    if total > MAX_ROWS:
        raise TooManyRowsError
    tabs = []
    n = 0
    for title, body in rows.items():
        if not body:
            continue
        keyed = []
        for row in body:
            n += 1  # keys are unique across tabs
            keyed.append([sanitize(c) for c in (f"{action.hex}-{n:04d}", *row)])
        tabs.append({"title": title, "header": LAYOUTS[title], "rows": keyed})
    payload = {
        "kind": KIND,
        "plan_id": str(plan_id),
        "plan_version": version,
        "content_hash": content_hash,
        "approval_id": str(approval_id),
        "tabs": tabs,
        "not_executed": not_executed,
    }
    return payload
