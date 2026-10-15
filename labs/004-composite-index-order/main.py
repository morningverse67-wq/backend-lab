"""
Lab 004: Composite index column order

Same two columns, two different orders: (user_id, created_at) vs
(created_at, user_id). Runs three queries against each index and against
no index, shows EXPLAIN QUERY PLAN, and measures how much the column
order changes the result. The "right" order depends on the query.

Run:
    python3 main.py
"""
import random
import sqlite3
import time

N_ORDERS = 300_000
N_USERS = 30_000
DAY = 86_400
START = 1_700_000_000           # fixed epoch, 365 days of data
random.seed(42)


def setup():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE orders (
            id         INTEGER PRIMARY KEY,
            user_id    INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            amount     INTEGER NOT NULL
        )
        """
    )
    conn.executemany(
        "INSERT INTO orders (user_id, created_at, amount) VALUES (?, ?, ?)",
        (
            (
                random.randint(1, N_USERS),
                START + random.randint(0, 365 * DAY - 1),
                random.randint(1_000, 100_000),
            )
            for _ in range(N_ORDERS)
        ),
    )
    conn.commit()
    return conn


USERS = [random.randint(1, N_USERS) for _ in range(100)]
LAST_30 = START + 335 * DAY      # last 30 days
LAST_1 = START + 364 * DAY       # last 1 day

CASES = [
    # name, sql, params for each call
    (
        "user + last 30d x100",
        "SELECT id, amount FROM orders WHERE user_id = ? AND created_at >= ?",
        [(u, LAST_30) for u in USERS],
    ),
    (
        "user only x100",
        "SELECT id, amount FROM orders WHERE user_id = ?",
        [(u,) for u in USERS],
    ),
    (
        "last 1d only x1",
        "SELECT id, amount FROM orders WHERE created_at >= ?",
        [(LAST_1,)],
    ),
]

INDEXES = [
    ("no index", None),
    ("(user_id, created_at)", "CREATE INDEX idx ON orders(user_id, created_at)"),
    ("(created_at, user_id)", "CREATE INDEX idx ON orders(created_at, user_id)"),
]


def plan(conn, sql, params):
    rows = conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    return " / ".join(r[-1] for r in rows)


def bench(conn, sql, params_list, repeat=3):
    best = float("inf")
    result = None
    for _ in range(repeat):
        start = time.perf_counter()
        result = [sorted(conn.execute(sql, p).fetchall()) for p in params_list]
        best = min(best, time.perf_counter() - start)
    return result, best * 1000


def main():
    conn = setup()
    print(f"orders={N_ORDERS:,}, users={N_USERS:,}, 365 days\n")

    times = {}   # (case, index) -> ms
    plans = {}   # (case, index) -> plan text
    rows = {}
    expected = {}
    for idx_name, ddl in INDEXES:
        conn.execute("DROP INDEX IF EXISTS idx")
        if ddl:
            conn.execute(ddl)
        conn.execute("ANALYZE")
        for name, sql, params in CASES:
            res, ms = bench(conn, sql, params)
            if name in expected:
                assert res == expected[name], (name, idx_name)
            else:
                expected[name] = res
            rows[name] = sum(len(r) for r in res)
            times[(name, idx_name)] = ms
            plans[(name, idx_name)] = plan(conn, sql, params[0])

    heads = [n for n, _ in INDEXES]
    print(f"{'case':<22} {'rows':>7} " + " ".join(f"{h:>22}" for h in heads))
    print("-" * (31 + 23 * len(heads)))
    for name, _, _ in CASES:
        cells = " ".join(f"{times[(name, h)]:>20.2f}ms" for h in heads)
        print(f"{name:<22} {rows[name]:>7,} {cells}")

    print("\nwhich order wins")
    for name, _, _ in CASES:
        a = times[(name, heads[1])]
        b = times[(name, heads[2])]
        winner, factor = (heads[1], b / a) if a <= b else (heads[2], a / b)
        print(f"- {name:<22} {winner} is {factor:,.1f}x faster")

    print("\nEXPLAIN QUERY PLAN")
    for name, _, _ in CASES:
        print(f"- {name}")
        for h in heads:
            print(f"    {h:<22}: {plans[(name, h)]}")


if __name__ == "__main__":
    main()
