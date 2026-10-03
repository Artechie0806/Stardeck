"""Deterministic demo retail database with intentionally cryptic columns.

Used for the built-in "Retail demo" data source, the eval suite and the frontend fixture.

Cryptic on purpose, to measure what user definitions are worth:
  customers.col1  -> age in years
  customers.sgmt  -> 1=Retail, 2=Wholesale, 3=Corporate
  orders.sts      -> 1=Completed, 2=Cancelled, 9=Refunded
  orders.amt      -> order total in paise (1 INR = 100 paise)
"""

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DEFINITIONS = Path(__file__).with_name("retail.definitions.yaml")
START = date(2024, 1, 1)
# Last complete month when the data was extended; no future-dated orders, so "this year" and
# "last 30 days" behave like real data.
END = date(2026, 9, 30)
N_CUSTOMERS = 450
N_ORDERS = 4500
CITIES = ["Mumbai", "Bengaluru", "Delhi", "Pune", "Chennai", "Hyderabad"]
# Markets differ a lot in size, like real sales data: share of customers, and how often they order.
# Mumbai ends up with roughly 20-30x Chennai's revenue, which maps and rankings make visible.
CITY_SHARE = {"Mumbai": 38, "Delhi": 24, "Bengaluru": 18, "Hyderabad": 10, "Pune": 6, "Chennai": 4}
CITY_ACTIVITY = {"Mumbai": 1.4, "Delhi": 1.2, "Bengaluru": 1.0, "Hyderabad": 0.8, "Pune": 0.7, "Chennai": 0.6}
CATEGORIES = {
    "Electronics": [("Earbuds", 1999), ("Power Bank", 1499), ("Smartwatch", 4999), ("Speaker", 2999)],
    "Home": [("Mixer", 3499), ("Kettle", 1299), ("Lamp", 899)],
    "Apparel": [("T-Shirt", 499), ("Jeans", 1599), ("Sneakers", 2999), ("Jacket", 3999)],
    "Grocery": [("Coffee", 450), ("Tea", 320), ("Almonds", 799)],
}


def build(path: Path, seed: int = 42) -> Path:
    rng = random.Random(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, city TEXT NOT NULL,
            col1 INTEGER, sgmt INTEGER NOT NULL, signup_date DATE NOT NULL);
        CREATE TABLE products (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, price INTEGER NOT NULL);
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
            order_date DATE NOT NULL, sts INTEGER NOT NULL, amt INTEGER NOT NULL);
        CREATE TABLE order_items (
            id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id),
            product_id INTEGER NOT NULL REFERENCES products(id),
            qty INTEGER NOT NULL, unit_price INTEGER NOT NULL);
        """
    )
    products = []
    for cat, items in CATEGORIES.items():
        for name, price in items:
            products.append((len(products) + 1, name, cat, price))
    db.executemany("INSERT INTO products VALUES (?,?,?,?)", products)

    # Customers keep signing up throughout, so active customers (and orders) grow year on year.
    signup_window = (END - START).days - 60
    customers = []
    for cid in range(1, N_CUSTOMERS + 1):
        customers.append((
            cid, f"Customer {cid}", rng.choices(CITIES, weights=[CITY_SHARE[c] for c in CITIES])[0], rng.randint(18, 70),
            rng.choices([1, 2, 3], weights=[70, 20, 10])[0],
            (START + timedelta(days=rng.randint(0, signup_window))).isoformat(),
        ))
    db.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?)", customers)

    iid = 0
    activity = [CITY_ACTIVITY[c[2]] for c in customers]
    for oid in range(1, N_ORDERS + 1):
        cust = rng.choices(customers, weights=activity)[0]
        signup = date.fromisoformat(cust[5])
        odate = signup + timedelta(days=rng.randint(0, max(0, (END - signup).days)))
        sts = rng.choices([1, 2, 9], weights=[85, 10, 5])[0]
        total = 0
        for prod in rng.sample(products, rng.randint(1, 3)):
            iid += 1
            qty = rng.randint(1, 3)
            db.execute("INSERT INTO order_items VALUES (?,?,?,?,?)", (iid, oid, prod[0], qty, prod[3]))
            total += qty * prod[3]
        db.execute("INSERT INTO orders VALUES (?,?,?,?,?)", (oid, cust[0], odate.isoformat(), sts, total * 100))
    db.commit()
    db.close()
    return path


def ensure(path: Path) -> Path:
    """Build the demo DB at `path` unless it already exists."""
    return path if path.exists() else build(path)
