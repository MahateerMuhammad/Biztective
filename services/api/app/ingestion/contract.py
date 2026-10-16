"""Canonical upload contract (DATA_CONTRACTS §2 as amended by AMD-8).

Single source of truth for file kinds, canonical columns, header synonyms, limits and
validation issue codes. Parsing, mapping, the pipeline and the API all read from here.
"""

import re
from dataclasses import dataclass
from typing import Literal

FileKind = Literal["products", "customers", "orders", "returns"]
FILE_KINDS: tuple[FileKind, ...] = ("products", "customers", "orders", "returns")

ColumnType = Literal[
    "id", "text", "dimension", "decimal", "int", "datetime", "date", "enum", "currency"
]

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_UPLOAD_BYTES = 60 * 1024 * 1024
MAX_ROWS: dict[FileKind, int] = {
    "products": 10_000,
    "customers": 100_000,
    "orders": 500_000,
    "returns": 200_000,
}
MAX_COLUMNS = 100
MAX_FIELD_CHARS = 10_000
MAX_DIMENSION_VALUES = 100
REJECTED_ROW_THRESHOLD = 0.05
STORED_ISSUES_PER_CODE = 10_000
MIN_HISTORY_WEEKS = 12
REASON_CODES = frozenset(
    {
        "damaged",
        "defective",
        "wrong_item",
        "not_as_described",
        "late_delivery",
        "changed_mind",
        "other",
    }
)


@dataclass(frozen=True)
class Column:
    name: str
    type: ColumnType
    required: bool
    synonyms: tuple[str, ...] = ()
    max_length: int = 64
    minimum: int | None = None  # for decimals/ints: inclusive lower bound (0 or 1)
    strictly_positive: bool = False
    maximum: int | None = None


COLUMNS: dict[FileKind, tuple[Column, ...]] = {
    "products": (
        Column("product_id", "id", True, ("sku", "product_code", "item_sku")),
        Column("product_name", "text", True, ("name", "title", "product_title"), max_length=200),
        Column("category", "dimension", True, ("product_category", "category_name")),
        Column(
            "unit_cost",
            "decimal",
            True,
            ("cost", "cogs", "unit_cogs", "cost_price"),
            minimum=0,
            maximum=1_000_000_000,
        ),
        Column(
            "list_price",
            "decimal",
            True,
            ("price", "msrp", "retail_price"),
            strictly_positive=True,
            maximum=1_000_000_000,
        ),
    ),
    "customers": (
        Column(
            "customer_id", "id", True, ("client_id", "customer_number", "customer_no", "customer")
        ),
        Column(
            "signup_date",
            "date",
            False,
            ("signup", "created_at", "joined_at", "registration_date", "registered_at"),
        ),
        Column("region", "dimension", False, ("customer_region",)),
        Column(
            "acquisition_channel",
            "dimension",
            False,
            ("channel", "source", "acquisition_source", "signup_channel"),
        ),
    ),
    "orders": (
        Column("order_line_id", "id", True, ("line_id", "order_item_id", "line_item_id")),
        Column("order_id", "id", True, ("order_number", "order_no", "order")),
        Column(
            "order_date",
            "datetime",
            True,
            (
                "order_datetime",
                "ordered_at",
                "order_timestamp",
                "date",
                "created_at",
                "purchase_date",
            ),
        ),
        Column("customer_id", "id", True, ("client_id", "customer_number", "customer")),
        Column("product_id", "id", True, ("sku", "product_code", "product")),
        Column(
            "quantity",
            "int",
            True,
            ("qty", "units", "quantity_ordered"),
            minimum=1,
            maximum=100_000,
        ),
        Column(
            "unit_price",
            "decimal",
            True,
            ("price", "item_price", "unit_price_amount"),
            minimum=0,
            maximum=1_000_000_000,
        ),
        Column(
            "discount_amount",
            "decimal",
            False,
            ("discount", "discount_value", "line_discount"),
            minimum=0,
            maximum=1_000_000_000_000,
        ),
        Column("channel", "dimension", False, ("sales_channel",)),
        Column("region", "dimension", False, ("sales_region", "ship_region", "shipping_region")),
        Column("currency", "currency", False, ("currency_code",)),
    ),
    "returns": (
        Column("return_id", "id", True, ("rma_id", "return_number", "rma")),
        Column("order_line_id", "id", True, ("line_id", "order_item_id", "line_item_id")),
        Column("return_date", "datetime", True, ("returned_at", "return_timestamp", "date")),
        Column(
            "quantity_returned",
            "int",
            True,
            ("quantity", "qty", "return_quantity", "units_returned"),
            minimum=1,
            maximum=100_000,
        ),
        Column(
            "refund_amount",
            "decimal",
            True,
            ("refund", "amount_refunded", "refund_value"),
            minimum=0,
            maximum=1_000_000_000_000,
        ),
        Column("reason_code", "enum", True, ("reason", "return_reason")),
        Column(
            "reason_text",
            "text",
            False,
            ("reason_detail", "comment", "comments", "notes"),
            max_length=500,
        ),
    ),
}

# PII-suspect headers are dropped before storage (SEC-22). Products are exempt: their `name`
# column is a product name, not personal data.
PII_SCAN_KINDS: frozenset[FileKind] = frozenset({"customers", "orders", "returns"})
_PII_TOKENS = frozenset(
    {
        "email",
        "mail",
        "phone",
        "mobile",
        "telephone",
        "address",
        "street",
        "postcode",
        "zip",
        "zipcode",
        "postal",
        "ssn",
        "dob",
        "birth",
        "birthday",
        "birthdate",
        "card",
        "iban",
        "ip",
        "passport",
        "surname",
    }
)


# "<thing>_name" columns that name a business object rather than a person.
_NON_PERSONAL_NAME_PREFIXES = frozenset(
    {"product", "category", "channel", "region", "store", "brand", "campaign", "item"}
)


def is_pii_header(normalized: str) -> bool:
    tokens = normalized.split("_")
    if any(t in _PII_TOKENS for t in tokens):
        return True
    if tokens[-1] == "name":
        return len(tokens) == 1 or tokens[0] not in _NON_PERSONAL_NAME_PREFIXES
    return False


_HEADER_SEPARATORS = re.compile(r"[\s\-./]+")
_HEADER_STRIP = re.compile(r"[^a-z0-9_]")


def normalize_header(raw: str) -> str:
    """'Order Date ' → 'order_date'; 'Unit-Price' → 'unit_price'."""
    value = _HEADER_SEPARATORS.sub("_", raw.strip().lstrip("﻿").lower())
    return _HEADER_STRIP.sub("", value).strip("_")


Severity = Literal["error", "warning"]

# code → (severity, description shown in the data-quality report)
ISSUE_CODES: dict[str, tuple[Severity, str]] = {
    "V-002": ("error", "File is not valid UTF-8 CSV or contains binary data."),
    "V-003": ("error", "File exceeds the row limit."),
    "V-006": ("error", "A dimension column has more than 100 distinct values."),
    "V-007": ("error", "No valid order lines remain after validation."),
    "V-010": ("error", "Malformed or empty CSV row (wrong number of fields)."),
    "V-011": ("error", "A required value is empty."),
    "V-012": ("error", "Identifier has an invalid format."),
    "V-013": (
        "error",
        "Date or time is invalid, ambiguous in format, nonexistent or out of range.",
    ),
    "V-014": (
        "error",
        "Number is invalid, negative where not allowed, has more than 4 decimals "
        "or is out of range.",
    ),
    "V-015": ("error", "Text is longer than allowed."),
    "V-016": ("error", "Value is not one of the allowed codes."),
    "V-017": ("error", "Currency is invalid or differs from the business currency."),
    "V-020": ("error", "Conflicting duplicate key: rows share a key but differ in content."),
    "V-021": ("warning", "Exact duplicate row ignored (the first occurrence is kept)."),
    "V-030": ("error", "Order line refers to a customer that is missing or invalid."),
    "V-031": ("error", "Order line refers to a product that is missing or invalid."),
    "V-032": ("error", "Return refers to an order line that is missing or invalid."),
    "V-033": ("error", "Discount exceeds the line's gross amount."),
    "V-034": ("error", "Return date is before the order date."),
    "V-035": ("error", "Returned quantity exceeds the quantity purchased on the line."),
    "V-036": ("error", "Refunds exceed the line's net amount."),
    "V-037": ("error", "Lines of the same order disagree on order date or customer."),
    "V-040": ("warning", "Unrecognised column ignored."),
    "V-041": ("warning", "Column that may contain personal data was dropped and not stored."),
    "V-042": (
        "warning",
        "Less than 12 complete weeks of order history; incident detection will need more data.",
    ),
    "V-043": (
        "warning",
        "Unit price is far from the product's list price (above 3x or below 0.1x).",
    ),
    "V-044": (
        "warning",
        "Ambiguous local time (daylight-saving change); the first occurrence was used.",
    ),
    "V-099": (
        "error",
        "More than 5% of the rows in this file were rejected; the dataset was not loaded.",
    ),
}
