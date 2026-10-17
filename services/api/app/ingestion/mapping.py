"""Header → canonical column mapping with a human checkpoint for anything uncertain (AMD-8).

Exact header matches are accepted automatically. Synonym matches are *proposals* and a column
with several candidates is *ambiguous*; both require confirmation before ingestion. Nothing
uncertain is ever applied silently.
"""

from collections import Counter
from typing import Any, Literal, TypedDict

from app.ingestion.contract import (
    COLUMNS,
    PII_SCAN_KINDS,
    FileKind,
    is_pii_header,
    normalize_header,
)

ColumnStatus = Literal["exact", "proposed", "ambiguous", "unmapped", "confirmed"]


class ColumnMapping(TypedDict):
    source: str | None
    status: ColumnStatus
    candidates: list[str]
    required: bool


class FileMapping(TypedDict):
    columns: dict[str, ColumnMapping]
    ignored: list[str]
    pii_dropped: list[str]
    needs_confirmation: bool


class MappingError(Exception):
    """A header problem the uploader must fix; carries per-column details."""

    def __init__(self, code: str, message: str, details: list[dict[str, str]]) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def normalize_headers(kind: FileKind, raw_headers: list[str]) -> list[str]:
    normalized = [normalize_header(h) for h in raw_headers]
    if any(not h for h in normalized):
        raise MappingError(
            "invalid_header",
            f"{kind}.csv has an empty column name.",
            [{"file": kind, "column": "", "problem": "empty column name"}],
        )
    duplicates = sorted(h for h, n in Counter(normalized).items() if n > 1)
    if duplicates:
        raise MappingError(
            "duplicate_header",
            f"{kind}.csv has duplicate column names.",
            [{"file": kind, "column": h, "problem": "duplicate column"} for h in duplicates],
        )
    return normalized


def _pii(kind: FileKind, headers: list[str]) -> list[str]:
    return [h for h in headers if kind in PII_SCAN_KINDS and is_pii_header(h)]


def _ignored(headers: list[str], pii: list[str], used: set[str]) -> list[str]:
    return [h for h in headers if h not in pii and h not in used]


def propose_mapping(kind: FileKind, headers: list[str]) -> FileMapping:
    pii = _pii(kind, headers)
    available = [h for h in headers if h not in pii]
    canonical_names = {c.name for c in COLUMNS[kind]}
    columns: dict[str, ColumnMapping] = {}
    for column in COLUMNS[kind]:
        req = column.required
        if column.name in available:
            columns[column.name] = {
                "source": column.name,
                "status": "exact",
                "candidates": [],
                "required": req,
            }
            continue
        candidates = [h for h in available if h in column.synonyms and h not in canonical_names]
        if len(candidates) == 1:
            columns[column.name] = {
                "source": candidates[0],
                "status": "proposed",
                "candidates": candidates,
                "required": req,
            }
        elif candidates:
            columns[column.name] = {
                "source": None,
                "status": "ambiguous",
                "candidates": candidates,
                "required": req,
            }
        else:
            columns[column.name] = {
                "source": None,
                "status": "unmapped",
                "candidates": [],
                "required": req,
            }

    # A header proposed for two canonical columns is ambiguous for both.
    proposed_sources = Counter(m["source"] for m in columns.values() if m["status"] == "proposed")
    for mapping in columns.values():
        if mapping["status"] == "proposed" and proposed_sources[mapping["source"]] > 1:
            mapping["status"] = "ambiguous"
            mapping["source"] = None

    # A required column with no known header name is offered every unused header, so the owner
    # can map their own naming ("Amount" → unit_price) instead of the file being refused.
    taken = {m["source"] for m in columns.values() if m["source"]} | {
        h for m in columns.values() for h in m["candidates"]
    }
    leftovers = [h for h in available if h not in taken]
    for column in COLUMNS[kind]:
        if column.required and columns[column.name]["status"] == "unmapped" and leftovers:
            columns[column.name]["status"] = "ambiguous"
            columns[column.name]["candidates"] = list(leftovers)

    missing = [
        c.name for c in COLUMNS[kind] if c.required and columns[c.name]["status"] == "unmapped"
    ]
    if missing:
        raise MappingError(
            "missing_required_columns",
            f"{kind}.csv is missing required columns: {', '.join(missing)}.",
            [
                {
                    "file": kind,
                    "column": name,
                    "problem": "required column not found",
                    "accepted_headers": ", ".join(
                        (name, *next(c.synonyms for c in COLUMNS[kind] if c.name == name))
                    ),
                }
                for name in missing
            ],
        )
    used = {m["source"] for m in columns.values() if m["source"]} | {
        h for m in columns.values() for h in m["candidates"]
    }
    return {
        "columns": columns,
        "ignored": _ignored(headers, pii, used),
        "pii_dropped": pii,
        "needs_confirmation": any(
            m["status"] in ("proposed", "ambiguous") for m in columns.values()
        ),
    }


def confirm_mapping(
    kind: FileKind, headers: list[str], current: FileMapping, selection: dict[str, str | None]
) -> FileMapping:
    """Apply a human's choices. Every proposed/ambiguous column must be decided explicitly."""
    problems: list[dict[str, str]] = []
    columns_by_name = {c.name: c for c in COLUMNS[kind]}
    # Stored headers no longer contain PII columns; keep the original record of what was dropped.
    pii = list(current["pii_dropped"])
    available = set(headers) - set(pii)

    for name in selection:
        if name not in columns_by_name:
            problems.append({"file": kind, "column": name, "problem": "unknown canonical column"})
    for name, mapping in current["columns"].items():
        if mapping["status"] in ("proposed", "ambiguous") and name not in selection:
            problems.append({"file": kind, "column": name, "problem": "decision required"})

    final: dict[str, ColumnMapping] = {}
    for name, column in columns_by_name.items():
        if name in selection:
            source = selection[name]
            if source is not None and source not in available:
                problems.append(
                    {"file": kind, "column": name, "problem": "source header not in file"}
                )
                source = None
            status: ColumnStatus = "confirmed" if source else "unmapped"
            final[name] = {
                "source": source,
                "status": status,
                "candidates": [],
                "required": column.required,
            }
        else:
            previous = current["columns"][name]
            final[name] = {
                "source": previous["source"],
                "status": previous["status"],
                "candidates": [],
                "required": column.required,
            }
        if column.required and final[name]["source"] is None:
            problems.append({"file": kind, "column": name, "problem": "required column unmapped"})

    sources = Counter(m["source"] for m in final.values() if m["source"])
    problems.extend(
        {"file": kind, "column": source, "problem": "header mapped to more than one column"}
        for source, count in sources.items()
        if count > 1
    )
    if problems:
        raise MappingError("invalid_mapping", f"The mapping for {kind}.csv is invalid.", problems)

    used = {source for m in final.values() if (source := m["source"]) is not None}
    return {
        "columns": final,
        "ignored": _ignored(headers, pii, used),
        "pii_dropped": pii,
        "needs_confirmation": False,
    }


def source_indices(headers: list[str], mapping: dict[str, Any]) -> dict[str, int]:
    """Canonical column → index in the CSV row, for mapped columns only."""
    position = {h: i for i, h in enumerate(headers)}
    return {
        name: position[m["source"]]
        for name, m in mapping["columns"].items()
        if m["source"] is not None
    }
