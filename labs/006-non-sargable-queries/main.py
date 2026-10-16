"""
Lab 006: Queries that cannot use an index (non-sargable predicates)

The column has an index, but the WHERE clause hides it:
  1. a function on the column      date(created_at) = ?
  2. a leading wildcard            phone LIKE '%1234'
  3. a type conversion on the col  CAST(order_no AS INTEGER) = ?
For each one, the same rows are fetched with a rewrite that the index
can serve, and EXPLAIN QUERY PLAN shows SCAN vs SEARCH.
Also checks where the fixes stop helping: an expression index only
matches the exact expression, and SQLite (unlike MySQL) converts the
constant instead of the column, so a plain type mismatch still uses
the index.

Run:
    python3 main.py
"""
import datetime as dt
import random
import sqlite3
import time

N_ORDERS = 300_000
CALLS = 100                      # different values per case
DAYS = 365
START = dt.datetime(2025, 10, 1)
random.seed(42)


def setup():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE orders (
            id          INTEGER PRIMARY KEY,
            order_no    TEXT    NOT NULL,
            phone       TEXT    NOT NULL,
            phone_last4 TEXT    NOT NULL,
            created_at  TEXT    NOT NULL,
            amount      INTEGER NOT NULL
        )
        """
    )
    order_nos = random.sample(range(10_000_000, 99_999_999), N_ORDERS)
    rows = []
    for no in order_nos:
        mid, last = random.randint(0, 9999), random.randint(0, 9999)
        ts = START + dt.timedelta(seconds=random.randint(0, DAYS * 86400 - 1))
        rows.append((
            str(no),
            f"010-{mid:04d}-{last:04d}",
            f"{last:04d}",
            ts.strftime("%Y-%m-%d %H:%M:%S"),
            random.randint(1_000, 100_000),
        ))
    conn.executemany(
        "INSERT INTO orders (order_no, phone, phone_last4, created_at, amount) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.execute("CREATE INDEX idx_orders_created ON orders(created_at)")
    conn.execute("CREATE INDEX idx_orders_phone ON orders(phone)")
    conn.execute("CREATE INDEX idx_orders_last4 ON orders(phone_last4)")
    conn.execute("CREATE INDEX idx_orders_order_no ON orders(order_no)")
    conn.execute("ANALYZE")
    conn.commit()
    return conn, order_nos


def plan(conn, sql, params):
    rows = conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    return " / ".join(r[-1] for r in rows)


def bench(conn, sql, params_list, repeat=3):
    best = float("inf")
    result = None
    for _ in range(repeat):
        start = time.perf_counter()
        result = [conn.execute(sql, p).fetchall() for p in params_list]
        best = min(best, time.perf_counter() - start)
    return result, best * 1000


def same(a, b):
    return [sorted(r) for r in a] == [sorted(r) for r in b]


def main():
    conn, order_nos = setup()
    print(f"orders={N_ORDERS:,}, each case = {CALLS} different values, best of 3, "
          f"SQLite {sqlite3.sqlite_version}\n")

    days = [(START + dt.timedelta(days=d)).strftime("%Y-%m-%d")
            for d in random.sample(range(DAYS), CALLS)]
    next_days = [(dt.date.fromisoformat(d) + dt.timedelta(days=1)).isoformat() for d in days]
    last4s = [f"{random.randint(0, 9999):04d}" for _ in range(CALLS)]
    nos = random.sample(order_nos, CALLS)

    cases = [
        (
            "function on column",
            "SELECT id, amount FROM orders WHERE date(created_at) = ?",
            [(d,) for d in days],
            "SELECT id, amount FROM orders WHERE created_at >= ? AND created_at < ?",
            list(zip(days, next_days)),
        ),
        (
            "LIKE '%1234'",
            "SELECT id, amount FROM orders WHERE phone LIKE ?",
            [("%-" + x,) for x in last4s],
            "SELECT id, amount FROM orders WHERE phone_last4 = ?",
            [(x,) for x in last4s],
        ),
        (
            "CAST(column)",
            "SELECT id, amount FROM orders WHERE CAST(order_no AS INTEGER) = ?",
            [(n,) for n in nos],
            "SELECT id, amount FROM orders WHERE order_no = ?",
            [(str(n),) for n in nos],
        ),
    ]

    print(f"{'case':<20} {'rows':>7} {'as written ms':>14} {'rewritten ms':>13} {'speedup':>9}")
    print("-" * 67)
    plans = []
    for name, bad_sql, bad_params, good_sql, good_params in cases:
        bad_res, bad_ms = bench(conn, bad_sql, bad_params)
        good_res, good_ms = bench(conn, good_sql, good_params)
        assert same(bad_res, good_res), name
        rows = sum(len(r) for r in good_res)
        assert rows > 0
        print(f"{name:<20} {rows:>7,} {bad_ms:>14.2f} {good_ms:>13.3f} {bad_ms / good_ms:>8.1f}x")
        plans.append((name, plan(conn, bad_sql, bad_params[0]), plan(conn, good_sql, good_params[0])))

    print("\nEXPLAIN QUERY PLAN")
    for name, b, g in plans:
        print(f"- {name}\n    as written: {b}\n    rewritten : {g}")

    # --- where the fixes stop helping -------------------------------------
    print("\nExpression index on date(created_at)")
    day_sql = "SELECT id, amount FROM orders WHERE date(created_at) = ?"
    month_sql = "SELECT id, amount FROM orders WHERE strftime('%Y-%m', created_at) = ?"
    month_range_sql = "SELECT id, amount FROM orders WHERE created_at >= ? AND created_at < ?"
    month = days[0][:7]
    y, m = map(int, month.split("-"))
    next_month = f"{y + (m == 12):04d}-{m % 12 + 1:02d}"

    day_before, day_before_ms = bench(conn, day_sql, [(d,) for d in days])
    month_before, month_before_ms = bench(conn, month_sql, [(month,)])
    conn.execute("CREATE INDEX idx_orders_day ON orders(date(created_at))")
    conn.execute("ANALYZE")
    day_after, day_after_ms = bench(conn, day_sql, [(d,) for d in days])
    month_after, month_after_ms = bench(conn, month_sql, [(month,)])
    month_range, month_range_ms = bench(conn, month_range_sql, [(month, next_month)])
    assert same(day_before, day_after)
    assert same(month_before, month_after) and same(month_after, month_range)
    print(f"  date(created_at) = ?  x{CALLS}: {day_before_ms:8.2f} ms -> {day_after_ms:8.2f} ms "
          f"({day_before_ms / day_after_ms:.1f}x)   {plan(conn, day_sql, (days[0],))}")
    print(f"  strftime month   = ?  x1 : {month_before_ms:8.2f} ms -> {month_after_ms:8.2f} ms "
          f"({month_before_ms / month_after_ms:.1f}x)   {plan(conn, month_sql, (month,))}")
    print(f"  month as range        x1 : {month_range_ms:8.2f} ms   "
          f"{plan(conn, month_range_sql, (month, next_month))}   rows={len(month_range[0]):,}")

    print("\nType mismatch without CAST: order_no (TEXT) = integer parameter")
    mismatch_sql = "SELECT id, amount FROM orders WHERE order_no = ?"
    mis_res, mis_ms = bench(conn, mismatch_sql, [(n,) for n in nos])
    str_res, str_ms = bench(conn, mismatch_sql, [(str(n),) for n in nos])
    assert same(mis_res, str_res)
    print(f"  integer param x{CALLS}: {mis_ms:.3f} ms   {plan(conn, mismatch_sql, (nos[0],))}")
    print(f"  string  param x{CALLS}: {str_ms:.3f} ms   {plan(conn, mismatch_sql, (str(nos[0]),))}")


if __name__ == "__main__":
    main()
