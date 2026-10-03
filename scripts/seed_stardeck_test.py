#!/usr/bin/env python3
"""Seed the `stardeck_test` MySQL database with clean e-commerce data.

Usage:
    # 1) Set env vars (or edit defaults below):
    export DB_HOST=localhost
    export DB_PORT=3306
    export DB_USER=root
    export DB_PASSWORD=your_password
    export DB_NAME=stardeck_test

    # 2) Run:
    python scripts/seed_stardeck_test.py

Tables created:
    customers       - 500 customers across Indian cities
    products        - 40 products in 6 categories
    categories      - 6 product categories
    orders          - 5,000 orders spanning 2024-10-03 to 2026-10-03
    order_items     - line items for each order

All data is deterministic (seeded with seed=42). No noise, no NULLs, no edge-case corruption.
"""

import os
import random
import sys
from datetime import date, timedelta
from pathlib import Path

try:
    import mysql.connector
except ImportError:
    print("Install with: pip install mysql-connector-python", file=sys.stderr)
    sys.exit(1)

# ── Config ──────────────────────────────────────────────────────────────────
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "stardeck_test")

SEED = 42
N_CUSTOMERS = 500
N_ORDERS = 5000
START_DATE = date(2024, 10, 3)
END_DATE = date(2026, 10, 3)
SPAN_DAYS = (END_DATE - START_DATE).days

CITIES = [
    "Mumbai", "Bengaluru", "Delhi", "Pune", "Chennai",
    "Hyderabad", "Kolkata", "Ahmedabad", "Jaipur", "Lucknow",
]

CATEGORIES = {
    "Electronics": [
        ("Wireless Earbuds", 1999),
        ("Power Bank 10000mAh", 1499),
        ("Smartwatch Pro", 4999),
        ("Bluetooth Speaker", 2999),
        ("USB-C Hub", 1799),
        ("Phone Case", 499),
    ],
    "Home & Kitchen": [
        ("Electric Kettle", 1299),
        ("Hand Blender", 2499),
        ("LED Desk Lamp", 899),
        ("Air Purifier", 5999),
        ("Mixer Grinder", 3499),
    ],
    "Fashion": [
        ("Cotton T-Shirt", 499),
        ("Slim Fit Jeans", 1599),
        ("Running Sneakers", 2999),
        ("Winter Jacket", 3999),
        ("Sunglasses", 1299),
    ],
    "Grocery": [
        ("Arabica Coffee 250g", 450),
        ("Masala Tea 100 bags", 320),
        ("Raw Almonds 500g", 799),
        ("Olive Oil 1L", 650),
        ("Dark Chocolate Box", 399),
    ],
    "Books": [
        ("Python Cookbook", 599),
        ("Data Science Handbook", 799),
        ("Business Strategy", 449),
        ("AI Fundamentals", 699),
    ],
    "Office Supplies": [
        ("Mechanical Keyboard", 2999),
        ("Ergonomic Mouse", 1499),
        ("Monitor Stand", 1999),
        ("Notebook Pack (5)", 349),
    ],
}

# Flatten products with IDs
PRODUCTS: list[tuple[int, str, str, int]] = []
_pid = 1
for cat, items in CATEGORIES.items():
    for name, price in items:
        PRODUCTS.append((_pid, name, cat, price))
        _pid += 1

# Order status mapping
STATUSES = [
    (1, "delivered", 0.80),
    (2, "shipped", 0.10),
    (3, "processing", 0.05),
    (4, "cancelled", 0.03),
    (5, "returned", 0.02),
]


def generate_customers(rng: random.Random) -> list:
    """Return list of customer rows."""
    customers = []
    for cid in range(1, N_CUSTOMERS + 1):
        name = f"Customer {cid:04d}"
        city = rng.choice(CITIES)
        email = f"user{cid}@example.com"
        phone = f"+91{rng.randint(7000000000, 9999999999)}"
        age = rng.randint(18, 65)
        gender = rng.choice(["M", "F", "O"])
        signup = START_DATE + timedelta(days=rng.randint(0, SPAN_DAYS - 30))
        customers.append((cid, name, email, phone, city, age, gender, signup))
    return customers


def generate_orders(rng: random.Random, customers: list) -> tuple:
    """Return (orders_rows, order_items_rows)."""
    orders = []
    items = []
    iid = 0
    for oid in range(1, N_ORDERS + 1):
        cust = rng.choice(customers)
        signup = cust[7]
        odate = signup + timedelta(days=rng.randint(0, max(1, (END_DATE - signup).days)))
        status_code, status_label, _ = rng.choices(STATUSES, weights=[s[2] for s in STATUSES])[0]

        n_items = rng.randint(1, 4)
        chosen_products = rng.sample(PRODUCTS, min(n_items, len(PRODUCTS)))
        total = 0
        for prod in chosen_products:
            iid += 1
            qty = rng.randint(1, 3)
            unit_price = prod[3]
            discount = rng.choice([0, 0, 0, 5, 10])  # 80% no discount
            final_price = int(unit_price * (1 - discount / 100))
            line_total = qty * final_price
            total += line_total
            items.append((iid, oid, prod[0], prod[1], qty, unit_price, final_price, line_total, discount))

        payment = rng.choice(["credit_card", "debit_card", "upi", "net_banking", "cod"])
        orders.append((oid, cust[0], cust[1], odate, status_code, status_label, total, payment, f"ORD-{oid:06d}"))

    return orders, items


def main():
    print(f"Connecting to MySQL: {DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}")
    conn = mysql.connector.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
    )
    cursor = conn.cursor()

    # Verify it's empty
    cursor.execute("SHOW TABLES")
    existing = cursor.fetchall()
    if existing:
        print(f"WARNING: Database '{DB_NAME}' already has tables:")
        for t in existing:
            print(f"  - {t[0]}")
        resp = input("Overwrite? (yes/no): ")
        if resp.lower() != "yes":
            print("Aborted.")
            conn.close()
            return

    # Create tables (mysql-connector-python doesn't support executescript)
    print("\nCreating tables...")
    for ddl in [
        "DROP TABLE IF EXISTS order_items",
        "DROP TABLE IF EXISTS orders",
        "DROP TABLE IF EXISTS customers",
        """CREATE TABLE customers (
            id INT PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            email VARCHAR(255) NOT NULL,
            phone VARCHAR(20) NOT NULL,
            city VARCHAR(100) NOT NULL,
            age INT NOT NULL,
            gender CHAR(1) NOT NULL,
            signup_date DATE NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
        """CREATE TABLE categories (
            id INT PRIMARY KEY AUTO_INCREMENT,
            name VARCHAR(100) NOT NULL UNIQUE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
        """CREATE TABLE products (
            id INT PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            category VARCHAR(100) NOT NULL,
            price INT NOT NULL,
            FOREIGN KEY (category) REFERENCES categories(name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
        """CREATE TABLE orders (
            id INT PRIMARY KEY,
            customer_id INT NOT NULL,
            customer_name VARCHAR(200) NOT NULL,
            order_date DATE NOT NULL,
            status_code INT NOT NULL,
            status VARCHAR(50) NOT NULL,
            total_amount INT NOT NULL,
            payment_method VARCHAR(50) NOT NULL,
            order_ref VARCHAR(20) NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
        """CREATE TABLE order_items (
            id INT PRIMARY KEY,
            order_id INT NOT NULL,
            product_id INT NOT NULL,
            product_name VARCHAR(255) NOT NULL,
            qty INT NOT NULL,
            unit_price INT NOT NULL,
            final_price INT NOT NULL,
            line_total INT NOT NULL,
            discount_pct INT NOT NULL DEFAULT 0,
            FOREIGN KEY (order_id) REFERENCES orders(id),
            FOREIGN KEY (product_id) REFERENCES products(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    ]:
        cursor.execute(ddl)
    conn.commit()

    # Insert data
    rng = random.Random(SEED)
    print(f"Generating {N_CUSTOMERS} customers, {N_ORDERS} orders...")
    customers = generate_customers(rng)
    orders, order_items = generate_orders(rng, customers)

    print("Inserting customers...")
    cursor.executemany(
        "INSERT INTO customers VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        customers,
    )

    print("Inserting categories...")
    cursor.executemany(
        "INSERT INTO categories (name) VALUES (%s)",
        [(cat,) for cat in CATEGORIES.keys()],
    )

    print("Inserting products...")
    cursor.executemany(
        "INSERT INTO products VALUES (%s, %s, %s, %s)",
        PRODUCTS,
    )

    print("Inserting orders...")
    cursor.executemany(
        "INSERT INTO orders VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        orders,
    )

    print("Inserting order items...")
    cursor.executemany(
        "INSERT INTO order_items VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        order_items,
    )

    conn.commit()

    # Summary
    print("\n" + "=" * 50)
    print(f"  Seeded '{DB_NAME}' successfully!")
    print("=" * 50)
    cursor.execute("SELECT COUNT(*) FROM customers")
    print(f"  Customers:    {cursor.fetchone()[0]}")
    cursor.execute("SELECT COUNT(*) FROM products")
    print(f"  Products:     {cursor.fetchone()[0]}")
    cursor.execute("SELECT COUNT(*) FROM categories")
    print(f"  Categories:   {cursor.fetchone()[0]}")
    cursor.execute("SELECT COUNT(*) FROM orders")
    print(f"  Orders:       {cursor.fetchone()[0]}")
    cursor.execute("SELECT COUNT(*) FROM order_items")
    print(f"  Order Items:  {cursor.fetchone()[0]}")
    cursor.execute("SELECT MIN(order_date), MAX(order_date) FROM orders")
    dmin, dmax = cursor.fetchone()
    print(f"  Date Range:   {dmin} to {dmax}")
    print("=" * 50)

    conn.close()


if __name__ == "__main__":
    main()
