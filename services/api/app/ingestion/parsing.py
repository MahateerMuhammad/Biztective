"""Strict, deterministic value parsers. Nothing is guessed: ambiguous input is rejected.

Each parser returns the typed value or raises `InvalidValueError` with an issue code and a short
human message. Messages never include more than a sanitised excerpt of the input.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.ingestion.contract import REASON_CODES, Column

_ID = re.compile(r"^[A-Za-z0-9._:\-]{1,64}$")
_DECIMAL = re.compile(r"^\d{1,15}(\.\d{1,4})?$")
_INT = re.compile(r"^\d{1,9}$")
_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_DATETIME = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(\.\d{1,6})?)?(Z|[+-]\d{2}:\d{2})?$"
)
_SLASHED_DATE = re.compile(r"^\d{1,4}[/.]\d{1,2}[/.]\d{1,4}")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_WHITESPACE = re.compile(r"\s+")
MIN_DATE = date(2000, 1, 1)


class InvalidValueError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class TimeContext:
    """How date-times are interpreted (AMD-8)."""

    source_tz: ZoneInfo  # naive date-times are read in this zone
    business_tz: ZoneInfo  # calendar dates are stored in this zone
    max_date: date  # latest acceptable business-local date


@dataclass(frozen=True)
class ParsedMoment:
    local_date: date
    at_utc: datetime | None
    ambiguous: bool


def clean_text(value: str) -> str:
    """Strip surrounding whitespace and control characters (keeps ordinary spaces)."""
    return "".join(ch for ch in value.strip() if ch in (" ",) or unicodedata.category(ch)[0] != "C")


def excerpt(value: str) -> str:
    return clean_text(value)[:100]


def parse_id(value: str) -> str:
    if not _ID.match(value):
        raise InvalidValueError(
            "V-012", "Use 1-64 characters: letters, digits, '.', '_', ':' or '-'."
        )
    return value


def parse_text(value: str, column: Column) -> str:
    cleaned = clean_text(value)
    if len(cleaned) > column.max_length:
        raise InvalidValueError("V-015", f"Longer than {column.max_length} characters.")
    return cleaned


def parse_dimension(value: str, column: Column) -> str:
    cleaned = _WHITESPACE.sub(" ", clean_text(value)).lower()
    if len(cleaned) > column.max_length:
        raise InvalidValueError("V-015", f"Longer than {column.max_length} characters.")
    return cleaned


def parse_decimal(value: str, column: Column) -> Decimal:
    if value.startswith("-") and _DECIMAL.match(value[1:]):
        raise InvalidValueError("V-014", "Negative values are not allowed.")
    if not _DECIMAL.match(value):
        raise InvalidValueError(
            "V-014", "Use plain digits with '.' and at most 4 decimals (no symbols or separators)."
        )
    number = Decimal(value)
    if column.strictly_positive and number <= 0:
        raise InvalidValueError("V-014", "Must be greater than zero.")
    if column.maximum is not None and number > column.maximum:
        raise InvalidValueError("V-014", f"Must not exceed {column.maximum}.")
    return number


def parse_int(value: str, column: Column) -> int:
    if not _INT.match(value):
        raise InvalidValueError("V-014", "Must be a whole number.")
    number = int(value)
    if column.minimum is not None and number < column.minimum:
        raise InvalidValueError("V-014", f"Must be at least {column.minimum}.")
    if column.maximum is not None and number > column.maximum:
        raise InvalidValueError("V-014", f"Must not exceed {column.maximum}.")
    return number


def parse_enum(value: str) -> str:
    code = value.strip().lower()
    if code not in REASON_CODES:
        raise InvalidValueError("V-016", "Allowed: " + ", ".join(sorted(REASON_CODES)) + ".")
    return code


def parse_currency(value: str, business_currency: str) -> str:
    code = value.strip().upper()
    if not _CURRENCY.match(code):
        raise InvalidValueError("V-017", "Use a 3-letter ISO 4217 code.")
    if code != business_currency:
        raise InvalidValueError(
            "V-017", f"Must be the business currency {business_currency} (no conversion)."
        )
    return code


def _check_range(local: date, ctx: TimeContext) -> date:
    if local < MIN_DATE or local > ctx.max_date:
        raise InvalidValueError("V-013", f"Must be between {MIN_DATE} and {ctx.max_date}.")
    return local


def parse_moment(value: str, ctx: TimeContext) -> ParsedMoment:
    """ISO 8601 only. Date-only values are business-local dates; times become UTC instants."""
    text = value.strip()
    if match := _DATE.match(text):
        try:
            local = date(int(match[1]), int(match[2]), int(match[3]))
        except ValueError as exc:
            raise InvalidValueError("V-013", "Not a real calendar date.") from exc
        return ParsedMoment(_check_range(local, ctx), None, False)

    match = _DATETIME.match(text)
    if not match:
        if _SLASHED_DATE.match(text):
            raise InvalidValueError("V-013", "Ambiguous date format; use YYYY-MM-DD.")
        raise InvalidValueError(
            "V-013", "Use ISO 8601: YYYY-MM-DD or YYYY-MM-DDTHH:MM[:SS][±HH:MM]."
        )
    year, month, day, hour, minute = (int(match[i]) for i in range(1, 6))
    second = int(match[6] or 0)
    micro = int((match[7] or ".0")[1:].ljust(6, "0"))
    try:
        naive = datetime(year, month, day, hour, minute, second, micro)
    except ValueError as exc:
        raise InvalidValueError("V-013", "Not a real date or time.") from exc

    ambiguous = False
    offset = match[8]
    if offset is None:
        aware = naive.replace(tzinfo=ctx.source_tz)
        # Nonexistent local times (spring-forward gap) do not survive a UTC round trip.
        if aware.astimezone(UTC).astimezone(ctx.source_tz).replace(tzinfo=None) != naive:
            raise InvalidValueError(
                "V-013", "This local time does not exist (daylight-saving change)."
            )
        ambiguous = aware.utcoffset() != naive.replace(tzinfo=ctx.source_tz, fold=1).utcoffset()
    elif offset == "Z":
        aware = naive.replace(tzinfo=UTC)
    else:
        sign = 1 if offset[0] == "+" else -1
        hours, minutes = int(offset[1:3]), int(offset[4:6])
        if hours > 18 or minutes > 59:
            raise InvalidValueError("V-013", "Invalid UTC offset.")
        delta = timedelta(hours=hours, minutes=minutes) * sign
        aware = (naive - delta).replace(tzinfo=UTC)
    at_utc = aware.astimezone(UTC)
    local = _check_range(at_utc.astimezone(ctx.business_tz).date(), ctx)
    return ParsedMoment(local, at_utc, ambiguous)
