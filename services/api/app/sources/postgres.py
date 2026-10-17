"""Read-only extraction from a business's PostgreSQL database into canonical CSV bytes.

The bytes feed the existing CSV ingestion pipeline unchanged; nothing here computes metrics.
"""

import csv
import io
import ipaddress
import re
import socket
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

import psycopg
from psycopg import sql

from app.ingestion.contract import COLUMNS, FILE_KINDS, MAX_ROWS, FileKind

IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
METADATA_ADDRESS = ipaddress.ip_address("169.254.169.254")
CONNECT_TIMEOUT_SECONDS = 10
SESSION_OPTIONS = "-c statement_timeout=20000 -c default_transaction_read_only=on"

MESSAGES: dict[str, tuple[int, str]] = {
    "source_unreachable": (502, "The database could not be reached."),
    "source_auth_failed": (422, "The database rejected the username or password."),
    "source_tls_failed": (502, "A secure (TLS) connection to the database could not be made."),
    "source_blocked_address": (422, "This database address is not allowed."),
    "source_missing_table": (422, "A configured table does not exist in the schema."),
    "source_permission_denied": (422, "The database user may not read a configured table."),
    "source_timeout": (504, "The database did not answer in time."),
    "source_too_large": (422, "A table has more rows than an upload allows."),
    "source_empty": (422, "A configured table has no usable columns or rows."),
    "invalid_identifier": (422, "Schema and table names must be lowercase SQL identifiers."),
}


class SourceError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
        self.status, self.message = MESSAGES[code]


@dataclass(frozen=True)
class Target:
    host: str
    port: int
    database: str
    username: str
    password: str
    sslmode: str
    sslrootcert: str
    allowed_hosts: frozenset[str]


def allowed_hosts(setting: str) -> frozenset[str]:
    return frozenset(h.strip().lower() for h in setting.split(",") if h.strip())


def check_identifier(name: str) -> str:
    if not IDENTIFIER.fullmatch(name):
        raise SourceError("invalid_identifier")
    return name


def _blocked(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return (
        address == METADATA_ADDRESS
        or address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def resolve_host(host: str, port: int, allowed: frozenset[str]) -> str:
    """Resolve once and return the address to connect to (no second lookup: no DNS rebinding)."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise SourceError("source_unreachable") from exc
    addresses = [ipaddress.ip_address(str(info[4][0]).split("%")[0]) for info in infos]
    if not addresses:
        raise SourceError("source_unreachable")
    if host.lower() not in allowed and any(_blocked(a) for a in addresses):
        raise SourceError("source_blocked_address")
    return str(addresses[0])


def _map_error(exc: psycopg.Error) -> SourceError:
    if isinstance(exc, psycopg.errors.QueryCanceled):
        return SourceError("source_timeout")
    if isinstance(exc, psycopg.errors.InsufficientPrivilege):
        return SourceError("source_permission_denied")
    if isinstance(
        exc, psycopg.errors.InvalidPassword | psycopg.errors.InvalidAuthorizationSpecification
    ):
        return SourceError("source_auth_failed")
    text = str(exc).lower()  # inspected only to classify; never returned to the client
    if "password authentication failed" in text or "no pg_hba.conf entry" in text:
        return SourceError("source_auth_failed")
    if "ssl" in text or "certificate" in text:
        return SourceError("source_tls_failed")
    if "timeout" in text:
        return SourceError("source_timeout")
    return SourceError("source_unreachable")


def connect(target: Target) -> psycopg.Connection[tuple[object, ...]]:
    address = resolve_host(target.host, target.port, target.allowed_hosts)
    try:
        return psycopg.connect(
            host=target.host,
            hostaddr=address,
            port=target.port,
            dbname=target.database,
            user=target.username,
            password=target.password,
            sslmode=target.sslmode,
            # libpq only accepts sslrootcert=system with verify-full; the CA matters only there.
            # (psycopg omits None parameters.)
            sslrootcert=target.sslrootcert if target.sslmode == "verify-full" else None,
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            options=SESSION_OPTIONS,
            application_name="root-source",
            autocommit=True,  # the explicit BEGIN READ ONLY opens the transaction
        )
    except psycopg.Error as exc:
        raise _map_error(exc) from None


def _tables(conn: psycopg.Connection[tuple[object, ...]], schema: str) -> list[str]:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = %s "
        "AND table_type IN ('BASE TABLE', 'VIEW') ORDER BY table_name",
        (schema,),
    ).fetchall()
    return [str(r[0]) for r in rows if IDENTIFIER.fullmatch(str(r[0]))]


def list_tables(target: Target, schema: str) -> list[str]:
    check_identifier(schema)
    try:
        with connect(target) as conn:
            conn.execute("BEGIN READ ONLY")
            return _tables(conn, schema)
    except psycopg.Error as exc:
        raise _map_error(exc) from None


def _csv(
    kind: FileKind, conn: psycopg.Connection[tuple[object, ...]], schema: str, table: str
) -> bytes:
    present = {
        str(r[0])
        for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s",
            (schema, table),
        ).fetchall()
    }
    columns = [c.name for c in COLUMNS[kind] if c.name in present]
    key = COLUMNS[kind][0].name
    if key not in columns:
        raise SourceError("source_empty")
    query = sql.SQL("SELECT {cols} FROM {schema}.{table} ORDER BY {key} LIMIT %s").format(
        cols=sql.SQL(", ").join(sql.Identifier(c) for c in columns),
        schema=sql.Identifier(schema),
        table=sql.Identifier(table),
        key=sql.Identifier(key),
    )
    rows = conn.execute(query, (MAX_ROWS[kind] + 1,)).fetchall()
    if len(rows) > MAX_ROWS[kind]:
        raise SourceError("source_too_large")
    if not rows:
        raise SourceError("source_empty")
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows([_cell(v) for v in row] for row in rows)
    return buffer.getvalue().encode("utf-8")


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def validate_tables(available: Sequence[str], tables: dict[FileKind, str]) -> None:
    for name in tables.values():
        check_identifier(name)
        if name not in available:
            raise SourceError("source_missing_table")


def extract(target: Target, schema: str, tables: dict[FileKind, str]) -> dict[FileKind, bytes]:
    check_identifier(schema)
    try:
        with connect(target) as conn:
            conn.execute("BEGIN READ ONLY")
            validate_tables(_tables(conn, schema), tables)
            return {kind: _csv(kind, conn, schema, tables[kind]) for kind in FILE_KINDS}
    except psycopg.Error as exc:
        raise _map_error(exc) from None
