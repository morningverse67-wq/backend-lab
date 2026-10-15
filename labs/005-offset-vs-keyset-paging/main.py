"""
Lab 005: OFFSET paging vs cursor (keyset) paging

Same "newest first" list, two ways to fetch a page:
  OFFSET : ORDER BY created_at DESC, id DESC LIMIT 20 OFFSET ?
  keyset : WHERE (created_at, id) < (?, ?) ORDER BY ... LIMIT 20
Measures how page depth changes the cost, what happens when a new row
arrives between two page requests, what a cursor without a tie-breaker
misses, and the case where OFFSET still wins (jumping to page N).

Run:
    python3 main.py
"""
import random
import sqlite3
import time

N_POSTS = 300_000
PAGE = 20
MINUTE = 60
START = 1_700_000_000            # fixed epoch
random.seed(42)

OFFSET_SQL = (
    "SELECT id, created_at, title FROM posts "
    "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
)
KEYSET_SQL = (
    "SELECT id, created_at, title FROM posts "
    "WHERE (created_at, id) < (?, ?) "
    "ORDER BY created_at DESC, id DESC LIMIT ?"
)
FIRST_SQL = (
    "SELECT id, created_at, title FROM posts "
    "ORDER BY created_at DESC, id DESC LIMIT ?"
)
# cursor on created_at only (no tie-breaker)
KEYSET_NO_TIE_SQL = (
    "SELECT id, created_at, title FROM posts "
    "WHERE created_at < ? "
    "ORDER BY created_at DESC, id DESC LIMIT ?"
)


def setup():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE posts (
            id         INTEGER PRIMARY KEY,
            created_at INTEGER NOT NULL,
            title      TEXT    NOT NULL
        )
        """
    )
    # 30 days, minute precision -> about 7 posts share each timestamp
    conn.executemany(
        "INSERT INTO posts (created_at, title) VALUES (?, ?)",
        (
            (START + random.randint(0, 30 * 24 * 60 - 1) * MINUTE, f"post {i}")
            for i in range(N_POSTS)
        ),
    )
    conn.execute("CREATE INDEX idx_posts_created ON posts(created_at, id)")
    conn.execute("ANALYZE")
    conn.commit()
    return conn


def plan(conn, sql, params):
    rows = conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    return " / ".join(r[-1] for r in rows)


def best_ms(fn, repeat=3):
    best = float("inf")
    result = None
    for _ in range(repeat):
        start = time.perf_counter()
        result = fn()
        best = min(best, time.perf_counter() - start)
    return result, best * 1000


def page_by_offset(conn, page_no):
    return conn.execute(OFFSET_SQL, (PAGE, (page_no - 1) * PAGE)).fetchall()


def page_by_cursor(conn, cursor):
    if cursor is None:
        return conn.execute(FIRST_SQL, (PAGE,)).fetchall()
    last_id, last_created = cursor[0], cursor[1]
    return conn.execute(KEYSET_SQL, (last_created, last_id, PAGE)).fetchall()


def depth_test(conn, pages, calls=100):
    print(f"1) page depth  (page size {PAGE}, each page fetched x{calls}, best of 3)")
    print(f"{'page':>8} {'offset':>9} {'OFFSET ms':>10} {'keyset ms':>10} {'ratio':>8}")
    print("-" * 49)
    for p in pages:
        offset = (p - 1) * PAGE
        # cursor = last row of the previous page (what the client would send back)
        cursor = None
        if p > 1:
            prev = page_by_offset(conn, p - 1)
            cursor = prev[-1]
        off_res, off_ms = best_ms(lambda: [page_by_offset(conn, p) for _ in range(calls)])
        key_res, key_ms = best_ms(lambda: [page_by_cursor(conn, cursor) for _ in range(calls)])
        assert off_res[0] == key_res[0] and len(off_res[0]) == PAGE, p
        print(f"{p:>8,} {offset:>9,} {off_ms:>10.2f} {key_ms:>10.2f} {off_ms / key_ms:>7.1f}x")
    print()


def new_row_test(conn):
    print("2) a new post arrives between page 1 and page 2")
    page1 = page_by_offset(conn, 1)
    cursor = page1[-1]
    conn.execute(
        "INSERT INTO posts (created_at, title) VALUES (?, ?)",
        (START + 30 * 24 * 60 * MINUTE, "brand new post"),
    )
    off_p2 = page_by_offset(conn, 2)
    key_p2 = page_by_cursor(conn, cursor)
    seen = {r[0] for r in page1}
    off_dup = sum(1 for r in off_p2 if r[0] in seen)
    key_dup = sum(1 for r in key_p2 if r[0] in seen)
    print(f"   OFFSET page 2 repeats {off_dup} row(s) from page 1")
    print(f"   keyset page 2 repeats {key_dup} row(s) from page 1")
    conn.execute("DELETE FROM posts WHERE title = 'brand new post'")
    print()
    return off_dup, key_dup


def tie_breaker_test(conn):
    print("3) walk every page with keyset: (created_at, id) cursor vs created_at-only cursor")
    total = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]

    seen, cursor, pages = set(), None, 0
    start = time.perf_counter()
    while True:
        rows = page_by_cursor(conn, cursor)
        if not rows:
            break
        seen.update(r[0] for r in rows)
        cursor = rows[-1]
        pages += 1
    full_ms = (time.perf_counter() - start) * 1000

    seen_nt, last_created = set(), None
    rows = conn.execute(FIRST_SQL, (PAGE,)).fetchall()
    while rows:
        seen_nt.update(r[0] for r in rows)
        last_created = rows[-1][1]
        rows = conn.execute(KEYSET_NO_TIE_SQL, (last_created, PAGE)).fetchall()

    assert len(seen) == total
    missed = total - len(seen_nt)
    print(f"   (created_at, id): {len(seen):,} / {total:,} rows, {pages:,} pages, {full_ms:.0f} ms")
    print(f"   created_at only : {len(seen_nt):,} / {total:,} rows -> missed {missed:,}")
    print()
    return missed


def jump_test(conn, target):
    print(f"4) jump straight to page {target:,} (no cursor yet)")
    off_res, off_ms = best_ms(lambda: page_by_offset(conn, target))

    def walk():
        cursor, rows = None, None
        for _ in range(target):
            rows = page_by_cursor(conn, cursor)
            cursor = rows[-1]
        return rows

    key_res, key_ms = best_ms(walk)
    assert off_res == key_res
    print(f"   OFFSET 1 query           : {off_ms:8.2f} ms")
    print(f"   keyset walk {target:,} pages : {key_ms:8.2f} ms  ({key_ms / off_ms:.1f}x slower)")
    print()


def main():
    conn = setup()
    print(f"posts={N_POSTS:,}, index on (created_at, id), "
          f"Python sqlite {sqlite3.sqlite_version}\n")

    depth_test(conn, [1, 100, 1_000, 10_000])
    new_row_test(conn)
    tie_breaker_test(conn)
    jump_test(conn, 10_000)

    print("EXPLAIN QUERY PLAN")
    print("  OFFSET :", plan(conn, OFFSET_SQL, (PAGE, 199_980)))
    print("  keyset :", plan(conn, KEYSET_SQL, (START, 1, PAGE)))


if __name__ == "__main__":
    main()
