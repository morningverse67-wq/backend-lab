"""
Lab 008: Bulk INSERT

Insert the same 10,000 rows into a file-backed SQLite database in different
ways and time each one:
  1. one INSERT per row, COMMIT after every row (autocommit)
  2. one INSERT per row, all inside one transaction
  3. executemany, one transaction
  4. multi-row VALUES (500 rows per statement), one transaction
Then the commit unit is varied (commit every 1 / 10 / 100 / 1,000 / 10,000
rows) to see where the gain levels off, and a failing row is injected to
show what each commit unit leaves behind.

A file database is used on purpose: with ":memory:" there is no journal and
no fsync, so the cost of COMMIT would not show up.

Run:
    python3 main.py
"""
import os
import random
import sqlite3
import sys
import tempfile
import time

N_ROWS = 10_000
MULTI_ROWS = 500                 # rows per multi-row INSERT statement
CHUNKS = [1, 10, 100, 1_000, 10_000]
FAIL_AT = 7_500                  # 0-based index of the row that breaks UNIQUE
random.seed(8)

INSERT = "INSERT INTO orders (order_no, user_id, amount, status) VALUES (?, ?, ?, ?)"


def make_rows(n):
    return [
        (
            f"ORD-{i:08d}",
            random.randint(1, 3_000),
            random.randint(1_000, 100_000),
            random.choice(["PAID", "SHIPPED", "CANCELLED"]),
        )
        for i in range(n)
    ]


def new_db(path):
    if os.path.exists(path):
        os.remove(path)
    # isolation_level=None: no implicit BEGIN, we write BEGIN/COMMIT ourselves
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute(
        """
        CREATE TABLE orders (
            id       INTEGER PRIMARY KEY,
            order_no TEXT    NOT NULL UNIQUE,
            user_id  INTEGER NOT NULL,
            amount   INTEGER NOT NULL,
            status   TEXT    NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX idx_orders_user ON orders(user_id)")
    return conn


def per_row_autocommit(conn, rows):
    for r in rows:
        conn.execute(INSERT, r)          # autocommit: every statement is its own transaction


def per_row_one_tx(conn, rows):
    conn.execute("BEGIN")
    for r in rows:
        conn.execute(INSERT, r)
    conn.execute("COMMIT")


def executemany_one_tx(conn, rows):
    conn.execute("BEGIN")
    conn.executemany(INSERT, rows)
    conn.execute("COMMIT")


def multi_values_one_tx(conn, rows):
    conn.execute("BEGIN")
    for i in range(0, len(rows), MULTI_ROWS):
        part = rows[i:i + MULTI_ROWS]
        sql = ("INSERT INTO orders (order_no, user_id, amount, status) VALUES "
               + ", ".join(["(?, ?, ?, ?)"] * len(part)))
        conn.execute(sql, [v for r in part for v in r])
    conn.execute("COMMIT")


def chunked(size):
    def run(conn, rows):
        for i in range(0, len(rows), size):
            conn.execute("BEGIN")
            conn.executemany(INSERT, rows[i:i + size])
            conn.execute("COMMIT")
    return run


def snapshot(conn):
    return conn.execute(
        "SELECT COUNT(*), SUM(amount), SUM(user_id), MIN(order_no), MAX(order_no) FROM orders"
    ).fetchone()


def bench(path, fn, rows, repeat=3):
    best = float("inf")
    state = None
    for _ in range(repeat):
        conn = new_db(path)
        start = time.perf_counter()
        fn(conn, rows)
        best = min(best, time.perf_counter() - start)
        state = snapshot(conn)
        conn.close()
    return best * 1000, state


def insert_until_failure(path, rows, size):
    """Insert in chunks of `size`; stop at the first error like a batch job would."""
    conn = new_db(path)
    try:
        for i in range(0, len(rows), size):
            conn.execute("BEGIN")
            try:
                conn.executemany(INSERT, rows[i:i + size])
                conn.execute("COMMIT")
            except sqlite3.IntegrityError:
                conn.execute("ROLLBACK")
                break
        return conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    finally:
        conn.close()


def main():
    rows = make_rows(N_ROWS)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "lab008.db")
        probe = new_db(path)
        journal = probe.execute("PRAGMA journal_mode").fetchone()[0]
        sync = probe.execute("PRAGMA synchronous").fetchone()[0]
        probe.close()
        print(f"rows={N_ROWS:,}, file DB (journal_mode={journal}, synchronous={sync}), "
              f"UNIQUE + 1 index, best of 3, Python {sys.version.split()[0]}, "
              f"SQLite {sqlite3.sqlite_version}\n")

        methods = [
            ("per row, commit each", per_row_autocommit),
            ("per row, 1 tx", per_row_one_tx),
            ("executemany, 1 tx", executemany_one_tx),
            (f"multi VALUES x{MULTI_ROWS}, 1 tx", multi_values_one_tx),
        ]
        print(f"{'method':<28} {'ms':>10} {'rows/s':>10} {'vs commit each':>15}")
        print("-" * 66)
        expected = None
        base = None
        for name, fn in methods:
            ms, state = bench(path, fn, rows)
            expected = expected or state
            assert state == expected and state[0] == N_ROWS, (name, state)
            base = base or ms
            print(f"{name:<28} {ms:>10.2f} {N_ROWS / ms * 1000:>10,.0f} {base / ms:>14.1f}x")

        print("\nCommit unit (executemany, COMMIT every N rows)")
        print(f"{'rows per commit':>15} {'commits':>8} {'ms':>10} {'vs 1 row':>9}")
        print("-" * 45)
        first = None
        for size in CHUNKS:
            ms, state = bench(path, chunked(size), rows)
            assert state == expected, (size, state)
            first = first or ms
            commits = -(-N_ROWS // size)
            print(f"{size:>15,} {commits:>8,} {ms:>10.2f} {first / ms:>8.1f}x")

        print(f"\nFailure: row #{FAIL_AT + 1:,} duplicates an order_no (UNIQUE)")
        bad = list(rows)
        bad[FAIL_AT] = rows[0]
        print(f"{'rows per commit':>15} {'rows saved':>11}")
        for size in [N_ROWS, 1_000, 100]:
            saved = insert_until_failure(path, bad, size)
            assert saved == (FAIL_AT // size) * size, (size, saved)
            print(f"{size:>15,} {saved:>11,}")


if __name__ == "__main__":
    main()
