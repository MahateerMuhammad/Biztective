"""Seeded synthetic dataset generator (FR-29). Development and test tooling only.

Runtime code must never import this module (AGENTS.md R-7). Output is byte-identical for the
same arguments, so tests and benchmarks are reproducible.

    uv run python -m scripts.generate_dataset --seed 7 --out /tmp/demo
"""

import argparse
import csv
import io
import random
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

ADJECTIVES = (
    "aero",
    "bright",
    "core",
    "dune",
    "echo",
    "flux",
    "glow",
    "halo",
    "iris",
    "jade",
    "kite",
    "lumen",
    "mesa",
    "nova",
    "onyx",
    "pulse",
    "quill",
    "rift",
    "sola",
    "terra",
)
NOUNS = (
    "blend",
    "brew",
    "case",
    "desk",
    "grip",
    "kit",
    "lamp",
    "mat",
    "pack",
    "pod",
    "set",
    "shade",
    "stand",
    "tote",
    "tray",
    "vase",
    "wrap",
)
CATEGORIES = ("kitchen", "office", "outdoor", "fitness", "home decor", "travel", "audio", "beauty")
REGIONS = ("north", "south", "east", "west", "central")
CHANNELS = ("web", "app", "marketplace")
ACQUISITION = ("search", "social", "referral", "email")
REASONS = (
    ("changed_mind", 30),
    ("damaged", 15),
    ("defective", 15),
    ("wrong_item", 10),
    ("not_as_described", 15),
    ("late_delivery", 10),
    ("other", 5),
)
CENT = Decimal("0.01")
Line = tuple[str, str, str, str, str, int, Decimal, Decimal, str, str]


@dataclass(frozen=True)
class Options:
    seed: int
    weeks: int
    customers: int
    products: int
    orders_per_day: int
    start: date
    noise: float
    pii_column: bool


def _money(value: float) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_EVEN)


def generate(options: Options) -> dict[str, bytes]:
    rng = random.Random(options.seed)
    end = options.start + timedelta(weeks=options.weeks) - timedelta(days=1)

    products: list[tuple[str, str, str, Decimal, Decimal]] = []
    for i in range(1, options.products + 1):
        cost = _money(rng.uniform(3, 120))
        price = _money(float(cost) * rng.uniform(1.4, 2.6))
        name = f"{rng.choice(ADJECTIVES).title()} {rng.choice(NOUNS).title()} {i:03d}"
        products.append((f"P{i:04d}", name, rng.choice(CATEGORIES), cost, price))

    customers: list[tuple[str, date, str, str]] = []
    for i in range(1, options.customers + 1):
        signup = options.start - timedelta(days=rng.randint(0, 400))
        customers.append((f"C{i:05d}", signup, rng.choice(REGIONS), rng.choice(ACQUISITION)))
    weights = [rng.paretovariate(1.5) for _ in customers]  # a few heavy repeat buyers

    order_rows: list[Line] = []
    lines: list[tuple[Line, date]] = []
    line_seq = order_seq = 0
    day = options.start
    while day <= end:
        weekday_factor = 1.25 if day.weekday() >= 5 else 1.0
        for _ in range(max(1, int(rng.gauss(options.orders_per_day * weekday_factor, 6)))):
            order_seq += 1
            customer = rng.choices(customers, weights=weights)[0]
            at = datetime(day.year, day.month, day.day, rng.randint(7, 22), rng.randint(0, 59))
            channel = rng.choice(CHANNELS)
            for _ in range(rng.choice((1, 1, 1, 2, 2, 3, 4))):
                line_seq += 1
                product = rng.choice(products)
                qty = rng.choice((1, 1, 1, 2, 2, 3))
                unit_price = _money(float(product[4]) * rng.uniform(0.95, 1.0))
                discount = (
                    _money(float(unit_price) * qty * 0.1) if rng.random() < 0.15 else Decimal(0)
                )
                line = (
                    f"L{line_seq:07d}",
                    f"O{order_seq:06d}",
                    at.isoformat(timespec="seconds"),
                    customer[0],
                    product[0],
                    qty,
                    unit_price,
                    discount,
                    channel,
                    customer[2],
                )
                order_rows.append(line)
                lines.append((line, day))
        day += timedelta(days=1)

    returns: list[tuple[object, ...]] = []
    for line, ordered in lines:
        if rng.random() >= 0.05:
            continue
        returned = ordered + timedelta(days=rng.randint(1, 20))
        if returned > end:
            continue
        qty_back = rng.randint(1, int(line[5]))
        net = line[6] * line[5] - line[7]
        refund = (net * Decimal(qty_back) / Decimal(line[5])).quantize(
            CENT, rounding=ROUND_HALF_EVEN
        )
        reason = rng.choices([r for r, _ in REASONS], weights=[w for _, w in REASONS])[0]
        returns.append(
            (
                f"R{len(returns) + 1:06d}",
                line[0],
                returned.isoformat(),
                qty_back,
                refund,
                reason,
                "",
            )
        )

    order_output: Sequence[tuple[object, ...]] = (
        _with_noise(rng, order_rows, options.noise) if options.noise else order_rows
    )

    customer_header = ["customer_id", "signup_date", "region", "acquisition_channel"]
    customer_rows: list[tuple[object, ...]] = list(customers)
    if options.pii_column:
        customer_header.append("email")
        customer_rows = [(*c, f"person{i}@example.test") for i, c in enumerate(customers, 1)]

    return {
        "products.csv": _csv(
            ["product_id", "product_name", "category", "unit_cost", "list_price"], products
        ),
        "customers.csv": _csv(customer_header, customer_rows),
        "orders.csv": _csv(
            [
                "order_line_id",
                "order_id",
                "order_date",
                "customer_id",
                "product_id",
                "quantity",
                "unit_price",
                "discount_amount",
                "channel",
                "region",
            ],
            order_output,
        ),
        "returns.csv": _csv(
            [
                "return_id",
                "order_line_id",
                "return_date",
                "quantity_returned",
                "refund_amount",
                "reason_code",
                "reason_text",
            ],
            returns,
        ),
    }


def _with_noise(
    rng: random.Random, rows: Sequence[tuple[object, ...]], rate: float
) -> list[tuple[object, ...]]:
    """Corrupt a fraction of order lines in ways the validator must catch."""
    noisy = []
    for row in rows:
        if rng.random() < rate:
            kind = rng.randrange(4)
            values = list(row)
            if kind == 0:
                values[2] = "03/04/2026"  # ambiguous date format
            elif kind == 1:
                values[5] = -1  # impossible quantity
            elif kind == 2:
                values[3] = "C99999"  # missing customer
            else:
                values[6] = "12,50"  # locale decimal
            row = tuple(values)
        noisy.append(row)
    return noisy


def _csv(header: list[str], rows: Sequence[tuple[object, ...]]) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue().encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--weeks", type=int, default=26)
    parser.add_argument("--customers", type=int, default=1500)
    parser.add_argument("--products", type=int, default=120)
    parser.add_argument("--orders-per-day", type=int, default=45)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 1, 5))
    parser.add_argument(
        "--noise", type=float, default=0.0, help="fraction of corrupted order lines"
    )
    parser.add_argument(
        "--pii-column", action="store_true", help="add an email column to customers"
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    files = generate(
        Options(
            args.seed,
            args.weeks,
            args.customers,
            args.products,
            args.orders_per_day,
            args.start,
            args.noise,
            args.pii_column,
        )
    )
    args.out.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (args.out / name).write_bytes(content)
    sys.stdout.write(", ".join(f"{n}: {c.count(b'\n') - 1} rows" for n, c in files.items()) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
