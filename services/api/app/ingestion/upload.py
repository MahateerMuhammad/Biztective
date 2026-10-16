"""Request-time inspection of uploaded files (SEC-07, SEC-22).

Each file is read once, in chunks, from Starlette's spooled temporary file: size, SHA-256,
strict incremental UTF-8 decoding, NUL bytes and binary signatures are checked in that pass.
The header row is read and mapped. If PII-suspect columns exist, the file is rewritten without
them so that personal data never reaches the database.
"""

import codecs
import csv
import hashlib
import io
import os
import unicodedata
from dataclasses import dataclass
from typing import BinaryIO

from app.ingestion.contract import MAX_COLUMNS, MAX_FIELD_CHARS, MAX_FILE_BYTES, FileKind
from app.ingestion.mapping import FileMapping, MappingError, normalize_headers, propose_mapping

CHUNK_BYTES = 1024 * 1024
HEADER_PREFIX_CHARS = 64 * 1024
ALLOWED_CONTENT_TYPES = frozenset(
    {
        "text/csv",
        "application/csv",
        "text/plain",
        "application/vnd.ms-excel",
        "application/octet-stream",
        "",
    }
)
_BINARY_SIGNATURES = (
    (b"PK\x03\x04", "a ZIP/XLSX archive"),
    (b"\xd0\xcf\x11\xe0", "a legacy Excel/Office file"),
    (b"%PDF", "a PDF document"),
    (b"\x1f\x8b", "a gzip archive"),
    (b"\xff\xfe", "UTF-16 text"),
    (b"\xfe\xff", "UTF-16 text"),
)

csv.field_size_limit(MAX_FIELD_CHARS)


class UploadError(Exception):
    def __init__(self, status: int, code: str, message: str, details: list[dict[str, str]]) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


@dataclass(frozen=True)
class InspectedFile:
    kind: FileKind
    filename: str
    size_bytes: int
    sha256: str  # of the bytes as uploaded (duplicate detection, provenance)
    content: bytes  # what is stored: as uploaded, or rewritten without PII columns
    headers: list[str]  # normalized headers of `content`
    mapping: FileMapping


def sanitize_filename(name: str | None) -> str:
    base = os.path.basename((name or "").replace("\\", "/"))
    cleaned = "".join(ch for ch in base if unicodedata.category(ch)[0] != "C").strip()
    return cleaned[:255] or "upload.csv"


def _fail(kind: FileKind, status: int, code: str, message: str) -> UploadError:
    return UploadError(status, code, message, [{"file": kind, "problem": message}])


def inspect_file(
    kind: FileKind, filename: str | None, content_type: str | None, stream: BinaryIO
) -> InspectedFile:
    name = sanitize_filename(filename)
    if not name.lower().endswith(".csv"):
        raise _fail(kind, 415, "invalid_file_type", f"{kind}: only .csv files are accepted.")
    media_type = (content_type or "").split(";")[0].strip().lower()
    if media_type not in ALLOWED_CONTENT_TYPES:
        raise _fail(kind, 415, "invalid_file_type", f"{kind}: content type is not CSV.")

    digest = hashlib.sha256()
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    buffer = bytearray()
    prefix: list[str] = []
    prefix_chars = 0
    while chunk := stream.read(CHUNK_BYTES):
        if not buffer:
            for signature, label in _BINARY_SIGNATURES:
                if chunk.startswith(signature):
                    raise _fail(kind, 422, "corrupt_file", f"{kind}: file is {label}, not CSV.")
        if len(buffer) + len(chunk) > MAX_FILE_BYTES:
            raise _fail(kind, 413, "file_too_large", f"{kind}: file exceeds 25 MB.")
        if b"\x00" in chunk:
            raise _fail(
                kind, 422, "corrupt_file", f"{kind}: file contains NUL bytes (binary data)."
            )
        try:
            text = decoder.decode(chunk)
        except UnicodeDecodeError as exc:
            offset = len(buffer) + exc.start
            raise _fail(
                kind, 422, "invalid_encoding", f"{kind}: not valid UTF-8 (byte offset {offset})."
            ) from exc
        if prefix_chars < HEADER_PREFIX_CHARS:
            prefix.append(text)
            prefix_chars += len(text)
        digest.update(chunk)
        buffer.extend(chunk)
    try:
        prefix.append(decoder.decode(b"", final=True))
    except UnicodeDecodeError as exc:
        raise _fail(kind, 422, "invalid_encoding", f"{kind}: not valid UTF-8 (truncated).") from exc

    if not buffer.strip():
        raise _fail(kind, 422, "empty_file", f"{kind}: file is empty.")
    head_text = "".join(prefix).lstrip("﻿")
    try:
        rows = csv.reader(io.StringIO(head_text, newline=""), strict=True)
        raw_headers = next(rows)
        has_data = next(rows, None) is not None or prefix_chars >= HEADER_PREFIX_CHARS
    except (csv.Error, StopIteration) as exc:
        raise _fail(kind, 422, "invalid_csv", f"{kind}: the header row is not valid CSV.") from exc
    if len(raw_headers) > MAX_COLUMNS:
        raise _fail(kind, 422, "invalid_csv", f"{kind}: more than {MAX_COLUMNS} columns.")
    if not has_data and kind != "returns":
        raise _fail(kind, 422, "empty_file", f"{kind}: file has a header but no data rows.")

    try:
        headers = normalize_headers(kind, raw_headers)
        mapping = propose_mapping(kind, headers)
    except MappingError as exc:
        raise UploadError(422, exc.code, exc.message, exc.details) from exc

    content = bytes(buffer)
    stored_headers = headers
    if mapping["pii_dropped"]:
        content, stored_headers = _without_columns(kind, content, headers, mapping["pii_dropped"])
    return InspectedFile(
        kind=kind,
        filename=name,
        size_bytes=len(buffer),
        sha256=digest.hexdigest(),
        content=content,
        headers=stored_headers,
        mapping=mapping,
    )


def _without_columns(
    kind: FileKind, content: bytes, headers: list[str], drop: list[str]
) -> tuple[bytes, list[str]]:
    """Rewrite the CSV without the dropped columns.

    A row whose field count differs from the header cannot be split safely, so it is replaced by
    an empty line; the pipeline reports empty lines as malformed rows (V-010) at the same row
    number, so no row is lost silently and no personal data is stored.
    """
    keep = [i for i, h in enumerate(headers) if h not in drop]
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    stream = io.TextIOWrapper(io.BytesIO(content), encoding="utf-8-sig", newline="")
    reader = csv.reader(stream, strict=True)
    try:
        next(reader)
        writer.writerow([headers[i] for i in keep])
        for row in reader:
            if len(row) == len(headers):
                writer.writerow([row[i] for i in keep])
            else:
                out.write("\n")
    except csv.Error as exc:
        raise _fail(
            kind, 422, "invalid_csv", f"{kind}: malformed CSV near row {reader.line_num}."
        ) from exc
    return out.getvalue().encode("utf-8"), [headers[i] for i in keep]
