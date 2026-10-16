import ast
import pathlib
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.ingestion.contract import COLUMNS, is_pii_header, normalize_header
from app.ingestion.export import safe_cell
from app.ingestion.mapping import MappingError, confirm_mapping, normalize_headers, propose_mapping
from app.ingestion.parsing import (
    InvalidValueError,
    TimeContext,
    clean_text,
    parse_currency,
    parse_decimal,
    parse_enum,
    parse_id,
    parse_int,
    parse_moment,
)
from scripts.generate_dataset import Options, generate

ORDERS = {c.name: c for c in COLUMNS["orders"]}
PRODUCTS = {c.name: c for c in COLUMNS["products"]}
CTX = TimeContext(ZoneInfo("America/New_York"), ZoneInfo("UTC"), date(2026, 10, 17))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" Order Date ", "order_date"),
        ("Unit-Price", "unit_price"),
        ("﻿product_id", "product_id"),
        ("Customer.E-mail", "customer_e_mail"),
        ("Qty (units)", "qty_units"),
    ],
)
def test_normalize_header(raw: str, expected: str) -> None:
    assert normalize_header(raw) == expected


@pytest.mark.parametrize(
    ("header", "pii"),
    [
        ("email", True),
        ("customer_email", True),
        ("customer_name", True),
        ("name", True),
        ("phone_number", True),
        ("shipping_address", True),
        ("ip", True),
        ("product_name", False),
        ("category_name", False),
        ("customer_id", False),
        ("region", False),
        ("zip_code", True),
    ],
)
def test_pii_header_rule(header: str, pii: bool) -> None:
    assert is_pii_header(header) is pii


def test_exact_headers_need_no_confirmation() -> None:
    headers = normalize_headers(
        "products", ["product_id", "product_name", "category", "unit_cost", "list_price"]
    )
    mapping = propose_mapping("products", headers)
    assert not mapping["needs_confirmation"]
    assert all(m["status"] == "exact" for m in mapping["columns"].values())


def test_synonym_is_a_proposal_and_two_candidates_are_ambiguous() -> None:
    headers = ["sku", "name", "category", "cost", "price", "msrp"]
    mapping = propose_mapping("products", headers)
    cols = mapping["columns"]
    assert cols["product_id"] == {
        "source": "sku",
        "status": "proposed",
        "candidates": ["sku"],
        "required": True,
    }
    assert cols["list_price"]["status"] == "ambiguous"
    assert cols["list_price"]["candidates"] == ["price", "msrp"]
    assert mapping["needs_confirmation"]
    assert mapping["pii_dropped"] == []  # products are exempt: "name" is a product name


def test_missing_required_and_duplicate_headers() -> None:
    with pytest.raises(MappingError) as missing:
        propose_mapping("orders", ["order_line_id", "order_id"])
    assert missing.value.code == "missing_required_columns"
    assert {d["column"] for d in missing.value.details} >= {"order_date", "quantity"}
    with pytest.raises(MappingError) as dup:
        normalize_headers("orders", ["Order ID", "order_id"])
    assert dup.value.code == "duplicate_header"
    with pytest.raises(MappingError):
        normalize_headers("orders", ["", "x"])


def test_confirmation_rules() -> None:
    headers = ["sku", "name", "category", "cost", "price", "msrp"]
    current = propose_mapping("products", headers)
    with pytest.raises(MappingError) as undecided:
        confirm_mapping("products", headers, current, {"product_id": "sku"})
    assert any(d["problem"] == "decision required" for d in undecided.value.details)
    with pytest.raises(MappingError) as reused:
        confirm_mapping(
            "products",
            headers,
            current,
            {"product_id": "sku", "list_price": "price", "unit_cost": "price"},
        )
    assert any("more than one" in d["problem"] for d in reused.value.details)
    with pytest.raises(MappingError) as unknown:
        confirm_mapping(
            "products",
            headers,
            current,
            {"product_id": "sku", "list_price": "nope", "bogus": "sku"},
        )
    problems = {d["problem"] for d in unknown.value.details}
    assert {"source header not in file", "unknown canonical column"} <= problems
    with pytest.raises(MappingError):
        confirm_mapping("products", headers, current, {"product_id": None, "list_price": "price"})
    final = confirm_mapping(
        "products",
        headers,
        current,
        {"product_id": "sku", "product_name": "name", "unit_cost": "cost", "list_price": "msrp"},
    )
    assert final["columns"]["list_price"] == {
        "source": "msrp",
        "status": "confirmed",
        "candidates": [],
        "required": True,
    }
    assert final["ignored"] == ["price"]
    assert not final["needs_confirmation"]


@pytest.mark.parametrize(
    ("raw", "local", "at"),
    [
        ("2026-03-02", date(2026, 3, 2), None),
        ("2026-03-02T10:15", date(2026, 3, 2), datetime(2026, 3, 2, 15, 15, tzinfo=UTC)),
        (
            "2026-03-02 23:30:05.5",
            date(2026, 3, 3),
            datetime(2026, 3, 3, 4, 30, 5, 500000, tzinfo=UTC),
        ),
        ("2026-03-02T10:15:00Z", date(2026, 3, 2), datetime(2026, 3, 2, 10, 15, tzinfo=UTC)),
        ("2026-03-02T01:00:00+05:30", date(2026, 3, 1), datetime(2026, 3, 1, 19, 30, tzinfo=UTC)),
    ],
)
def test_moments(raw: str, local: date, at: datetime | None) -> None:
    moment = parse_moment(raw, CTX)
    assert (moment.local_date, moment.at_utc, moment.ambiguous) == (local, at, False)


@pytest.mark.parametrize(
    "raw",
    [
        "03/04/2026",
        "2026/03/04",
        "4.3.2026",
        "2026-13-01",
        "2026-02-29",
        "1999-12-31",
        "2026-10-19",
        "2026-03-08T02:30",
        "2026-03-02T25:00",
        "2026-03-02T10:15+19:00",
        "yesterday",
        "2026-3-2",
    ],
)
def test_invalid_moments(raw: str) -> None:
    with pytest.raises(InvalidValueError) as problem:
        parse_moment(raw, CTX)
    assert problem.value.code == "V-013"


def test_ambiguous_local_time_is_flagged() -> None:
    moment = parse_moment("2025-11-02T01:30", CTX)
    assert moment.ambiguous
    assert moment.at_utc == datetime(2025, 11, 2, 5, 30, tzinfo=UTC)


@pytest.mark.parametrize("raw", ["-1.00", "1,000", "$5", "1e3", "NaN", "Infinity", "1.23456", ""])
def test_invalid_decimals(raw: str) -> None:
    with pytest.raises(InvalidValueError):
        parse_decimal(raw, ORDERS["unit_price"])


def test_decimal_bounds() -> None:
    assert parse_decimal("0.0001", ORDERS["unit_price"]) == Decimal("0.0001")
    with pytest.raises(InvalidValueError):
        parse_decimal("0", PRODUCTS["list_price"])  # strictly positive
    with pytest.raises(InvalidValueError):
        parse_decimal("1000000001", ORDERS["unit_price"])


@pytest.mark.parametrize(
    ("raw", "ok"),
    [("1", True), ("100000", True), ("0", False), ("100001", False), ("1.0", False), ("-3", False)],
)
def test_quantities(raw: str, ok: bool) -> None:
    if ok:
        assert parse_int(raw, ORDERS["quantity"]) == int(raw)
    else:
        with pytest.raises(InvalidValueError):
            parse_int(raw, ORDERS["quantity"])


def test_ids_enums_currency_and_text() -> None:
    assert parse_id("L-0001:a.b_c") == "L-0001:a.b_c"
    for bad in ("has space", "x" * 65, "ümlaut", "a/b"):
        with pytest.raises(InvalidValueError):
            parse_id(bad)
    assert parse_enum(" Damaged ") == "damaged"
    with pytest.raises(InvalidValueError):
        parse_enum("broken")
    assert parse_currency("usd", "USD") == "USD"
    for bad in ("EUR", "US", "U$D"):
        with pytest.raises(InvalidValueError):
            parse_currency(bad, "USD")
    assert clean_text("  a\tb\x00c​ ") == "abc​" or clean_text("  a\tb\x00c ") == "abc"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("=1+1", "'=1+1"),
        ("+cmd", "'+cmd"),
        ("-2", "'-2"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\tx", "'\tx"),
        ("\rx", "'\rx"),
        ("safe", "safe"),
        (None, ""),
        (5, "5"),
        ("", ""),
    ],
)
def test_safe_cell(value: str | int | None, expected: str) -> None:
    assert safe_cell(value) == expected


def test_generator_is_deterministic() -> None:
    options = Options(
        seed=11,
        weeks=3,
        customers=50,
        products=10,
        orders_per_day=5,
        start=date(2026, 1, 5),
        noise=0.05,
        pii_column=True,
    )
    assert generate(options) == generate(options)
    other = generate(Options(**{**options.__dict__, "seed": 12}))
    assert other["orders.csv"] != generate(options)["orders.csv"]


def test_runtime_code_never_imports_the_generator() -> None:
    app_dir = pathlib.Path(__file__).resolve().parents[2] / "app"
    for path in app_dir.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("scripts"), path
            if isinstance(node, ast.Import):
                assert all(not a.name.startswith("scripts") for a in node.names), path
