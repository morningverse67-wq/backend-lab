"""
Lab 009: COUNT(*) cost and alternatives

COUNT(*) has to visit every matching row (or index entry), so its cost grows
with the table. This lab measures:
  1. COUNT(*) on the whole table at 10k / 100k / 1M rows vs reading a
     counter row kept up to date by triggers
  2. COUNT(*) WHERE status = 'PAID' with no index, with an index on status,
     and from the counter table
  3. "Is there at least one?" written as COUNT(*) > 0 vs EXISTS, both when a
     match is found early and when there is no match at all
  4. What the counter table costs: INSERT/UPDATE/DELETE time with and without
     the triggers, and a check that the counters still equal COUNT(*)

Run:
    python3 main.py
"""
import random
import sqlite3
import sys
import time

SIZES = [10_000, 100_000, 1_000_000]
CALLS = 10            # queries per measurement; time reported per call
REPEAT = 3            # best of 3
WRITE_ROWS = 20_000
STATUSES = ["PAID"] * 6 + ["SHIPPED"] * 3 + ["CANCELLED"]
random.seed(9)

COUNTER_DDL = """
CREATE TABLE order_counts (status TEXT PRIMARY KEY, cnt INTEGER NOT NULL);
CREATE TRIGGER trg_orders_ins AFTER INSERT ON orders BEGIN
    INSERT INTO order_counts (status, cnt) VALUES (NEW.status, 1)
        ON CONFLICT(status) DO UPDATE SET cnt = cnt + 1;
    UPDATE order_counts SET cnt = cnt + 1 WHERE status = '*';
END;
CREATE TRIGGER trg_orders_del AFTER DELETE ON orders BEGIN
    UPDATE order_counts SET cnt = cnt - 1 WHERE status IN (OLD.status, '*');
END;
CREATE TRIGGER trg_orders_upd AFTER UPDATE OF status ON orders
WHEN OLD.status <> NEW.status BEGIN
    UPDATE order_counts SET cnt = cnt - 1 WHERE status = OLD.status;
    INSERT INTO order_counts (status, cnt) VALUES (NEW.status, 1)
        ON CONFLICT(status) DO UPDATE SET cnt = cnt + 1;
END;
"""


def make_rows(n, start=0):
    return [
        (start + i + 1, random.randint(1, 50_000), random.randint(1_000, 100_000),
         random.choice(STATUSES))
        for i in range(n)
    ]


def new_db(rows, counter=False, status_index=False):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
        "amount INTEGER NOT NULL, status TEXT NOT NULL)"
    )
    conn.execute("CREATE INDEX idx_orders_user ON orders(user_id)")
    if status_index:
        conn.execute("CREATE INDEX idx_orders_status ON orders(status)")
    if counter:
        conn.executescript(COUNTER_DDL)
        conn.execute("INSERT INTO order_counts VALUES ('*', 0)")
    conn.executemany("INSERT INTO orders VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    return conn


def per_call_ms(conn, sql, params=()):
    best = float("inf")
    result = None
    for _ in range(REPEAT):
        start = time.perf_counter()
        for _ in range(CALLS):
            result = conn.execute(sql, params).fetchone()[0]
        best = min(best, time.perf_counter() - start)
    return best / CALLS * 1000, result


def plan(conn, sql, params=()):
    return " / ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql, params))


def timed(fn):
    start = time.perf_counter()
    fn()
    return (time.perf_counter() - start) * 1000


def main():
    print(f"in-memory SQLite, {CALLS} calls per measurement, ms per call, best of {REPEAT}, "
          f"Python {sys.version.split()[0]}, SQLite {sqlite3.sqlite_version}\n")

    # 1. whole-table COUNT(*) as the table grows
    print("1) SELECT COUNT(*) FROM orders vs counter row")
    print(f"{'rows':>10} {'COUNT(*) ms':>12} {'counter ms':>11} {'factor':>9}")
    print("-" * 46)
    big = None
    for n in SIZES:
        conn = new_db(make_rows(n), counter=True)
        c_ms, c_val = per_call_ms(conn, "SELECT COUNT(*) FROM orders")
        k_ms, k_val = per_call_ms(conn, "SELECT cnt FROM order_counts WHERE status = '*'")
        assert c_val == k_val == n, (n, c_val, k_val)
        print(f"{n:>10,} {c_ms:>12.4f} {k_ms:>11.4f} {c_ms / k_ms:>8.1f}x")
        if n == SIZES[-1]:
            big = conn
        else:
            conn.close()
    print("   plan COUNT(*):", plan(big, "SELECT COUNT(*) FROM orders"))

    # 2. conditional count
    n = SIZES[-1]
    q = "SELECT COUNT(*) FROM orders WHERE status = ?"
    print(f"\n2) COUNT(*) WHERE status = 'PAID' ({n:,} rows)")
    scan_ms, scan_val = per_call_ms(big, q, ("PAID",))
    scan_plan = plan(big, q, ("PAID",))
    cnt_ms, cnt_val = per_call_ms(big, "SELECT cnt FROM order_counts WHERE status = ?", ("PAID",))
    big.execute("CREATE INDEX idx_orders_status ON orders(status)")
    big.execute("ANALYZE")
    idx_ms, idx_val = per_call_ms(big, q, ("PAID",))
    idx_plan = plan(big, q, ("PAID",))
    assert scan_val == idx_val == cnt_val, (scan_val, idx_val, cnt_val)
    print(f"   matching rows: {scan_val:,}")
    print(f"{'how':<22} {'ms':>10} {'vs no index':>12}")
    print("-" * 46)
    for name, ms in [("no index (scan)", scan_ms), ("index on status", idx_ms),
                     ("counter table", cnt_ms)]:
        print(f"{name:<22} {ms:>10.4f} {scan_ms / ms:>11.1f}x")
    print("   plan no index :", scan_plan)
    print("   plan index    :", idx_plan)

    # 3. existence check on a column without an index
    print(f"\n3) 'is there at least one?' on amount (no index, {n:,} rows)")
    print(f"{'case':<26} {'COUNT>0 ms':>11} {'EXISTS ms':>10} {'factor':>8}")
    print("-" * 58)
    for label, threshold in [("match early (amount>=50k)", 50_000),
                             ("no match (amount>=1M)", 1_000_000)]:
        c_ms, c_val = per_call_ms(big, "SELECT COUNT(*) > 0 FROM orders WHERE amount >= ?",
                                  (threshold,))
        e_ms, e_val = per_call_ms(big, "SELECT EXISTS (SELECT 1 FROM orders WHERE amount >= ?)",
                                  (threshold,))
        assert c_val == e_val, (label, c_val, e_val)
        print(f"{label:<26} {c_ms:>11.4f} {e_ms:>10.4f} {c_ms / e_ms:>7.1f}x  -> {bool(e_val)}")
    big.close()

    # 4. write cost of keeping the counter
    print(f"\n4) write cost of the counter triggers (start from 100,000 rows)")
    print(f"{'operation':<30} {'plain ms':>9} {'+counter ms':>12} {'slower':>8}")
    print("-" * 62)
    base_rows = make_rows(100_000)
    extra = make_rows(WRITE_ROWS, start=100_000)
    results = {}
    for counter in (False, True):
        best = {}
        for _ in range(REPEAT):
            conn = new_db(base_rows, counter=counter)
            ops = [
                (f"INSERT {WRITE_ROWS:,} rows", lambda: (conn.executemany(
                    "INSERT INTO orders VALUES (?, ?, ?, ?)", extra), conn.commit())),
                ("UPDATE status of 10,000 rows", lambda: (conn.execute(
                    "UPDATE orders SET status = 'CANCELLED' WHERE id <= 10000"), conn.commit())),
                ("DELETE 10,000 rows", lambda: (conn.execute(
                    "DELETE FROM orders WHERE id > 110000"), conn.commit())),
            ]
            for name, fn in ops:
                best[name] = min(best.get(name, float("inf")), timed(fn))
            if counter:
                truth = dict(conn.execute("SELECT status, COUNT(*) FROM orders GROUP BY status"))
                truth["*"] = sum(truth.values())
                kept = {s: c for s, c in conn.execute("SELECT status, cnt FROM order_counts") if c}
                assert kept == truth, (kept, truth)
            conn.close()
        results[counter] = best
    for name in results[False]:
        p, c = results[False][name], results[True][name]
        print(f"{name:<30} {p:>9.2f} {c:>12.2f} {c / p:>7.2f}x")
    print("   counters == COUNT(*) GROUP BY status after all writes: OK")


if __name__ == "__main__":
    main()
