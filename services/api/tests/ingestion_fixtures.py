"""Small, hand-checkable CSV fixtures for ingestion tests."""

# ruff: noqa: E501 - CSV header lines must stay on one line

PRODUCTS = """product_id,product_name,category,unit_cost,list_price
P1,Aero Lamp,Home Decor,10.00,25.00
P2,Core Mat,Fitness,4.5000,12.00
P3,Echo Tray,Kitchen,2.25,6.50
"""

CUSTOMERS = """customer_id,signup_date,region,acquisition_channel
C1,2025-12-01,North,Search
C2,2025-12-15,South,
C3,,,
"""

ORDERS = """order_line_id,order_id,order_date,customer_id,product_id,quantity,unit_price,discount_amount,channel,region
L1,O1,2026-03-02T10:15:00,C1,P1,2,25.00,5.00,Web,North
L2,O1,2026-03-02T10:15:00,C1,P2,1,12.00,0,Web,North
L3,O2,2026-03-03,C2,P3,3,6.50,,App,
L4,O3,2026-03-04T23:30:00-05:00,C3,P1,1,24.00,0,,South
"""

RETURNS = """return_id,order_line_id,return_date,quantity_returned,refund_amount,reason_code,reason_text
R1,L1,2026-03-05,1,22.50,damaged,Box crushed
R2,L3,2026-03-10,2,13.00,changed_mind,
"""


def dataset(**overrides: str) -> dict[str, bytes]:
    files = {"products": PRODUCTS, "customers": CUSTOMERS, "orders": ORDERS, "returns": RETURNS}
    files.update(overrides)
    return {kind: content.encode() for kind, content in files.items()}


def with_rows(base: str, *rows: str) -> str:
    return base + "".join(row + "\n" for row in rows)
