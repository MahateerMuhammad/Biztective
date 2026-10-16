"""Spreadsheet-safe CSV cells (SEC-16, OWASP CSV injection)."""

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: str | int | None) -> str:
    """Render a cell so spreadsheet applications never evaluate it as a formula."""
    text = "" if value is None else str(value)
    if text.startswith(FORMULA_PREFIXES):
        return "'" + text
    return text
