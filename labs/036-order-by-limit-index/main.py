"""036. 정렬 인덱스로 ORDER BY ... LIMIT의 정렬 단계 없애기 (sqlite3, 표준 라이브러리만 사용)."""
import argparse
import platform
import random
import sqlite3
import statistics
import time
from pathlib import Path
from xml.sax.saxutils import escape

ROWS = 200_000       # 주문 행
USERS = 200          # 회원 수 (회원당 평균 1,000건)
QUERIES = 1_000      # "회원 한 명의 최근 주문 20건" 요청 수
LIMIT = 20
REPEATS = 5
TABLE = "CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, created_at INTEGER NOT NULL, amount INTEGER NOT NULL)"
INDEXES = {
    "user": "CREATE INDEX ix_orders ON orders(user_id)",
    "user_created": "CREATE INDEX ix_orders ON orders(user_id, created_at)",
}
Q = "SELECT id, created_at FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT ?"


def build(kind):
    rng = random.Random(36)
    con = sqlite3.connect(":memory:", isolation_level=None)
    con.execute(TABLE)
    rows = [(i, rng.randrange(USERS), 1_700_000_000 + rng.randrange(10_000_000), rng.randrange(1, 100_000)) for i in range(ROWS)]
    con.execute("BEGIN")
    con.executemany("INSERT INTO orders VALUES (?,?,?,?)", rows)
    con.execute("COMMIT")
    con.execute(INDEXES[kind])
    con.execute("ANALYZE")
    return con


def plan(con):
    return " / ".join(r[-1] for r in con.execute("EXPLAIN QUERY PLAN " + Q, (1, LIMIT)))


def rows_visited(con):
    """SQLite VM 단계 수(sqlite3 progress handler, 1단계마다 호출) — 요청 1번에 드는 일의 양."""
    steps = [0]
    def tick():
        steps[0] += 1
        return 0
    con.set_progress_handler(tick, 1)
    con.execute(Q, (7, LIMIT)).fetchall()
    con.set_progress_handler(None, 0)
    return steps[0]


def timed(con):
    rng = random.Random(360)
    users = [rng.randrange(USERS) for _ in range(QUERIES)]
    runs = []
    for _ in range(REPEATS):
        t = time.perf_counter()
        for u in users:
            assert len(con.execute(Q, (u, LIMIT)).fetchall()) == LIMIT
        runs.append((time.perf_counter() - t) * 1000)
    return statistics.median(runs)


def run(kind):
    con = build(kind)
    p = plan(con)
    return {"plan": p, "temp_sort": "TEMP B-TREE" in p, "steps": rows_visited(con), "ms": timed(con),
            "result": con.execute(Q, (7, LIMIT)).fetchall()}


def regression_tests(res):
    assert res["user"]["temp_sort"] and not res["user_created"]["temp_sort"]
    assert res["user"]["result"] == res["user_created"]["result"]   # 같은 20건을 같은 순서로
    r = res["user_created"]["result"]
    assert all(r[i][1] >= r[i + 1][1] for i in range(len(r) - 1))


def experiment():
    res = {k: run(k) for k in ("user", "user_created")}
    regression_tests(res)
    for k, name in (("user", "INDEX(user_id)"), ("user_created", "INDEX(user_id, created_at)")):
        v = res[k]
        print(f"| {name} | {'있음' if v['temp_sort'] else '없음'} | {v['steps']:,} | {v['ms']:,.1f} |  plan: {v['plan']}")
    return res


def write_svg(res):
    w, h = 900, 360
    rows = [("INDEX(user_id)", res["user"], "#dc2626"), ("INDEX(user_id, created_at)", res["user_created"], "#2563eb")]
    metrics = [("SQLite VM steps for one request", "steps", "{:,.0f}"),
               (f"ms for {QUERIES:,} requests (median of {REPEATS})", "ms", "{:,.1f}")]
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">',
         '<title id="t">ORDER BY created_at DESC LIMIT 20: index without vs with the sort column</title>',
         f'<desc id="d">{ROWS:,} orders, {USERS} users. INDEX(user_id) needs a temp B-tree sort; INDEX(user_id, created_at) does not.</desc>',
         f'<rect width="{w}" height="{h}" fill="#f8fafc"/><g font-family="sans-serif" fill="#0f172a">',
         f'<text x="28" y="40" font-size="24" font-weight="bold">Latest {LIMIT} orders of one user ({ROWS:,} rows)</text>']
    for i, (label, key, fmt) in enumerate(metrics):
        y0 = 90 + i * 110
        mx = max(r[1][key] for r in rows)
        s.append(f'<text x="28" y="{y0}" font-size="16" font-weight="bold">{escape(label)}</text>')
        for j, (name, vals, color) in enumerate(rows):
            y = y0 + 12 + j * 30
            bw = vals[key] / mx * 420 if mx else 0
            tag = " (temp sort)" if vals["temp_sort"] else " (no sort)"
            s.append(f'<text x="28" y="{y + 15}" font-size="13">{escape(name + tag)}</text>')
            s.append(f'<rect x="330" y="{y}" width="{bw:.1f}" height="20" fill="{color}"/>')
            s.append(f'<text x="{336 + bw:.1f}" y="{y + 15}" font-size="14" fill="{color}">{fmt.format(vals[key])}</text>')
    s.append(f'<text x="28" y="{h - 16}" font-size="13">Python {platform.python_version()}, SQLite {sqlite3.sqlite_version}; time varies by machine.</text></g></svg>')
    Path(__file__).with_name("result.svg").write_text("\n".join(s) + "\n", encoding="utf-8")
    print("wrote result.svg")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write-svg", action="store_true")
    a = ap.parse_args()
    if not __debug__:
        raise SystemExit("Run without -O")
    print(f"Python {platform.python_version()} | SQLite {sqlite3.sqlite_version} | {platform.system()} {platform.machine()}")
    print("| 인덱스 | 정렬 단계(TEMP B-TREE) | 요청 1번 VM 단계 | 요청 1,000번 ms |")
    res = experiment()
    if a.write_svg:
        write_svg(res)
