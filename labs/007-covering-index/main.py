"""
Lab 007: Covering index

The same lookup, served by two kinds of index:
  plain    : INDEX(user_id)                    -> find rows in the index,
             then read every matching row from the table
  covering : INDEX(user_id, created_at, amount) -> every selected column is
             already in the index, so the table is never read
Two identical databases are built (same rows, different indexes) and the
same queries are timed on both. EXPLAIN QUERY PLAN shows
"USING INDEX" vs "USING COVERING INDEX".
Also checks where the covering index stops helping: a query that needs a
column outside the index, and the cost on the write side (index size,
INSERT time).

Run:
    python3 main.py
"""
import datetime as dt
import random
import sqlite3
import time

N_ORDERS = 300_000
N_USERS = 3_000                  # about 100 orders per user
CALLS = 100                      # different users per lookup case
RANGE_CALLS = 10                 # different months for the range case
N_INSERT = 30_000
START = dt.datetime(2025, 10, 1)
DAYS = 365
random.seed(7)

INDEXES = {
    "plain": [
        "CREATE INDEX idx_user ON orders(user_id)",
        "CREATE INDEX idx_created ON orders(created_at)",
    ],
    "covering": [
        "CREATE INDEX idx_user_cov ON orders(user_id, created_at, amount)",
        "CREATE INDEX idx_created_cov ON orders(created_at, amount)",
    ],
}


def make_rows(n):
    rows = []
    for _ in range(n):
        ts = START + dt.timedelta(seconds=random.randint(0, DAYS * 86400 - 1))
        rows.append((
            random.randint(1, N_USERS),
            ts.strftime("%Y-%m-%d %H:%M:%S"),
            random.randint(1_000, 100_000),
            random.choice(["PAID", "SHIPPED", "CANCELLED"]),
            "".join(random.choices("abcdefghijklmnopqrstuvwxyz ", k=200)),  # address, memo ...
        ))
    return rows


def setup(kind, rows):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE orders (
            id         INTEGER PRIMARY KEY,
            user_id    INTEGER NOT NULL,
            created_at TEXT    NOT NULL,
            amount     INTEGER NOT NULL,
            status     TEXT    NOT NULL,
            memo       TEXT    NOT NULL
        )
        """
    )
    conn.executemany(
        "INSERT INTO orders (user_id, created_at, amount, status, memo) VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    table_pages = conn.execute("PRAGMA page_count").fetchone()[0]
    for sql in INDEXES[kind]:
        conn.execute(sql)
    conn.execute("ANALYZE")
    conn.commit()
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    index_mb = (conn.execute("PRAGMA page_count").fetchone()[0] - table_pages) * page_size / 1024 / 1024
    return conn, index_mb


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


def bench_insert(conn, rows, repeat=3):
    best = float("inf")
    for _ in range(repeat):
        conn.execute("SAVEPOINT s")
        start = time.perf_counter()
        conn.executemany(
            "INSERT INTO orders (user_id, created_at, amount, status, memo) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        best = min(best, time.perf_counter() - start)
        conn.execute("ROLLBACK TO s")
        conn.execute("RELEASE s")
    return best * 1000


def same(a, b):
    return [sorted(r) for r in a] == [sorted(r) for r in b]


def main():
    rows = make_rows(N_ORDERS)
    plain, plain_mb = setup("plain", rows)
    cov, cov_mb = setup("covering", rows)
    print(f"orders={N_ORDERS:,}, users={N_USERS:,}, memo=200 chars, best of 3, "
          f"SQLite {sqlite3.sqlite_version}\n")

    users = random.sample(range(1, N_USERS + 1), CALLS)
    months = []
    for m in random.sample(range(12), RANGE_CALLS):
        y, mo = 2025 + (10 + m - 1) // 12, (10 + m - 1) % 12 + 1
        ny, nmo = y + (mo == 12), mo % 12 + 1
        months.append((f"{y:04d}-{mo:02d}-01", f"{ny:04d}-{nmo:02d}-01"))

    cases = [
        (
            f"user orders x{CALLS}",
            "SELECT created_at, amount FROM orders WHERE user_id = ?",
            [(u,) for u in users],
        ),
        (
            f"month SUM x{RANGE_CALLS}",
            "SELECT SUM(amount) FROM orders WHERE created_at >= ? AND created_at < ?",
            months,
        ),
        (
            f"+memo x{CALLS}",
            "SELECT created_at, amount, memo FROM orders WHERE user_id = ?",
            [(u,) for u in users],
        ),
    ]

    print(f"{'case':<18} {'rows':>8} {'plain ms':>10} {'covering ms':>12} {'speedup':>8}")
    print("-" * 60)
    plans = []
    for name, sql, params in cases:
        p_res, p_ms = bench(plain, sql, params)
        c_res, c_ms = bench(cov, sql, params)
        assert same(p_res, c_res), name
        n = sum(len(r) for r in c_res) if "SUM" not in sql else sum(
            plain.execute("SELECT COUNT(*) FROM orders WHERE created_at >= ? AND created_at < ?", p).fetchone()[0]
            for p in params)
        assert n > 0
        print(f"{name:<18} {n:>8,} {p_ms:>10.2f} {c_ms:>12.2f} {p_ms / c_ms:>7.1f}x")
        plans.append((name, plan(plain, sql, params[0]), plan(cov, sql, params[0])))

    print("\nEXPLAIN QUERY PLAN")
    for name, p, c in plans:
        print(f"- {name}\n    plain   : {p}\n    covering: {c}")

    print("\nWrite side")
    new_rows = make_rows(N_INSERT)
    p_ins = bench_insert(plain, new_rows)
    c_ins = bench_insert(cov, new_rows)
    assert plain.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == N_ORDERS
    assert cov.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == N_ORDERS
    print(f"  index size      : plain {plain_mb:.2f} MB, covering {cov_mb:.2f} MB "
          f"({cov_mb / plain_mb:.2f}x)")
    print(f"  INSERT {N_INSERT:,} rows: plain {p_ins:.2f} ms, covering {c_ins:.2f} ms "
          f"({c_ins / p_ins:.2f}x)")


if __name__ == "__main__":
    main()
