"""
Lab 002: Index vs full table scan

Same query, same data, with and without an index on the WHERE column.
Shows EXPLAIN QUERY PLAN output and measures how much an index helps
for a selective lookup, and how little it helps when the query returns
most of the table.

Run:
    python3 main.py
"""
import random
import sqlite3
import time

N_ORDERS = 300_000
N_USERS = 30_000
random.seed(42)


def setup():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE orders (
            id         INTEGER PRIMARY KEY,
            user_id    INTEGER NOT NULL,
            status     TEXT    NOT NULL,
            amount     INTEGER NOT NULL
        )
        """
    )
    statuses = ["PAID"] * 9 + ["CANCELLED"]  # 90% PAID, 10% CANCELLED
    conn.executemany(
        "INSERT INTO orders (user_id, status, amount) VALUES (?, ?, ?)",
        (
            (random.randint(1, N_USERS), random.choice(statuses), random.randint(1_000, 100_000))
            for _ in range(N_ORDERS)
        ),
    )
    conn.commit()
    return conn


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


CASES = [
    # name, sql, params for each call
    (
        "user lookup x100",
        "SELECT id, amount FROM orders WHERE user_id = ?",
        [(random.randint(1, N_USERS),) for _ in range(100)],
    ),
    (
        "status=PAID x1",
        "SELECT id, amount FROM orders WHERE status = ?",
        [("PAID",)],
    ),
]


def main():
    conn = setup()
    print(f"orders={N_ORDERS:,}, users={N_USERS:,}\n")

    before = {}
    for name, sql, params in CASES:
        before[name] = (plan(conn, sql, params[0]), *bench(conn, sql, params))

    conn.execute("CREATE INDEX idx_orders_user_id ON orders(user_id)")
    conn.execute("CREATE INDEX idx_orders_status ON orders(status)")
    conn.execute("ANALYZE")

    print(f"{'case':<18} {'rows':>8} {'no index ms':>12} {'index ms':>10} {'speedup':>8}")
    print("-" * 60)
    plans = []
    for name, sql, params in CASES:
        plan_before, res_before, ms_before = before[name]
        plan_after = plan(conn, sql, params[0])
        res_after, ms_after = bench(conn, sql, params)
        assert [sorted(r) for r in res_before] == [sorted(r) for r in res_after]
        rows = sum(len(r) for r in res_after)
        print(f"{name:<18} {rows:>8,} {ms_before:>12.2f} {ms_after:>10.2f} {ms_before / ms_after:>7.1f}x")
        plans.append((name, plan_before, plan_after))

    print("\nEXPLAIN QUERY PLAN")
    for name, b, a in plans:
        print(f"- {name}\n    before: {b}\n    after : {a}")


if __name__ == "__main__":
    main()
