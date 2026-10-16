"""Reference dataset with independently, hand-computed expected results (see test comments).

Weeks: W1 = 2026-01-05..01-11 (Mon-Sun), W2 = 2026-01-12..01-18.
P3 has no unit cost. L5 is cancelled. L4 (order O3) is fully refunded. R3 is a partial refund.
"""

# ruff: noqa: E501
PRODUCTS = """product_id,product_name,category,unit_cost,list_price
P1,Lamp,Home,10.00,25.00
P2,Mat,Fitness,4.00,12.00
P3,Tray,Kitchen,,6.50
"""
CUSTOMERS = """customer_id,region
C1,north
C2,south
C3,south
"""
ORDERS = """order_line_id,order_id,order_date,customer_id,product_id,quantity,unit_price,discount_amount,channel,region,status
L1,O1,2026-01-05,C1,P1,2,25.00,5.00,web,north,completed
L2,O1,2026-01-05,C1,P2,1,12.00,0,web,north,
L3,O2,2026-01-06,C2,P1,1,25.00,0,app,south,completed
L4,O3,2026-01-12,C1,P2,3,12.00,6.00,web,north,completed
L5,O4,2026-01-13,C3,P1,1,25.00,0,web,south,cancelled
L6,O5,2026-01-14,C2,P3,2,6.50,0,app,south,completed
"""
RETURNS = """return_id,order_line_id,return_date,quantity_returned,refund_amount,reason_code
R1,L1,2026-01-07,1,22.50,damaged
R2,L4,2026-01-15,3,30.00,changed_mind
R3,L3,2026-01-16,1,10.00,changed_mind
"""


def files() -> dict[str, bytes]:
    return {
        "products": PRODUCTS.encode(),
        "customers": CUSTOMERS.encode(),
        "orders": ORDERS.encode(),
        "returns": RETURNS.encode(),
    }


# M21 fixture: as-of 2026-01-31, maturity 14 days → only lines ordered by 2026-01-17 count, and
# only refunds within 14 days of the order date. L1 (net 45) has R1 at +2 days (22.50) and a late
# return R9 at +20 days (10.00, excluded). L2 (net 12) is not returned. Expected M21 for P1:
# 22.50 / 45 = 0.5; for P2: 0 / 12 = 0; overall: 22.50 / 57.
MATURED_ORDERS = """order_line_id,order_id,order_date,customer_id,product_id,quantity,unit_price,discount_amount
L1,O1,2026-01-05,C1,P1,2,25.00,5.00
L2,O1,2026-01-05,C1,P2,1,12.00,0
L9,O9,2026-01-31,C2,P2,1,12.00,0
"""
MATURED_RETURNS = """return_id,order_line_id,return_date,quantity_returned,refund_amount,reason_code
R1,L1,2026-01-07,1,22.50,damaged
R9,L1,2026-01-25,1,10.00,changed_mind
"""


def matured_files() -> dict[str, bytes]:
    return {
        "products": PRODUCTS.encode(),
        "customers": CUSTOMERS.encode(),
        "orders": MATURED_ORDERS.encode(),
        "returns": MATURED_RETURNS.encode(),
    }
