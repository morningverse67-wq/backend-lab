"""
Lab 001: N+1 query problem

Reproduces the N+1 pattern with SQLite (stdlib only) and compares it with
two fixes: a single JOIN and a batched IN query.

Run:
    python3 main.py
"""
import sqlite3
import time
from collections import defaultdict

N_POSTS = 500
COMMENTS_PER_POST = 5


class QueryCounter:
    """Counts SELECTs. Optionally sleeps per query to mimic a DB round trip."""

    def __init__(self, rtt_ms=0.0):
        self.count = 0
        self.rtt_s = rtt_ms / 1000

    def __call__(self, statement):
        if statement.strip().upper().startswith("SELECT"):
            self.count += 1
            if self.rtt_s:
                time.sleep(self.rtt_s)


def setup():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE post (
            id    INTEGER PRIMARY KEY,
            title TEXT NOT NULL
        );
        CREATE TABLE comment (
            id      INTEGER PRIMARY KEY,
            post_id INTEGER NOT NULL REFERENCES post(id),
            body    TEXT NOT NULL
        );
        CREATE INDEX idx_comment_post_id ON comment(post_id);
        """
    )
    conn.executemany(
        "INSERT INTO post (id, title) VALUES (?, ?)",
        [(i, f"post-{i}") for i in range(1, N_POSTS + 1)],
    )
    conn.executemany(
        "INSERT INTO comment (post_id, body) VALUES (?, ?)",
        [
            (p, f"comment-{p}-{c}")
            for p in range(1, N_POSTS + 1)
            for c in range(COMMENTS_PER_POST)
        ],
    )
    conn.commit()
    return conn


def n_plus_one(conn):
    """1 query for posts + N queries for each post's comments."""
    result = {}
    for post_id, title in conn.execute("SELECT id, title FROM post"):
        rows = conn.execute(
            "SELECT body FROM comment WHERE post_id = ?", (post_id,)
        ).fetchall()
        result[post_id] = (title, [r[0] for r in rows])
    return result


def join_once(conn):
    """1 query: JOIN and group in application code."""
    result = {}
    rows = conn.execute(
        """
        SELECT p.id, p.title, c.body
        FROM post p
        LEFT JOIN comment c ON c.post_id = p.id
        ORDER BY p.id
        """
    )
    for post_id, title, body in rows:
        entry = result.setdefault(post_id, (title, []))
        if body is not None:
            entry[1].append(body)
    return result


def batch_in(conn):
    """2 queries: posts, then all comments with WHERE post_id IN (...)."""
    posts = conn.execute("SELECT id, title FROM post").fetchall()
    ids = [p[0] for p in posts]
    placeholders = ",".join("?" * len(ids))
    comments = defaultdict(list)
    for post_id, body in conn.execute(
        f"SELECT post_id, body FROM comment WHERE post_id IN ({placeholders})",
        ids,
    ):
        comments[post_id].append(body)
    return {pid: (title, comments[pid]) for pid, title in posts}


def measure(conn, fn, rtt_ms=0.0, repeat=3):
    counter = QueryCounter(rtt_ms)
    conn.set_trace_callback(counter)
    best = float("inf")
    result = None
    for _ in range(repeat):
        counter.count = 0
        start = time.perf_counter()
        result = fn(conn)
        best = min(best, time.perf_counter() - start)
    conn.set_trace_callback(None)
    return result, counter.count, best * 1000


STRATEGIES = [("n_plus_one", n_plus_one), ("join_once", join_once), ("batch_in", batch_in)]


def main():
    conn = setup()
    print(f"posts={N_POSTS}, comments/post={COMMENTS_PER_POST}")

    baseline = None
    for rtt_ms in (0.0, 0.5):
        label = "in-memory, no network" if rtt_ms == 0 else f"simulated round trip {rtt_ms} ms/query"
        print(f"\n[{label}]")
        print(f"{'strategy':<12} {'queries':>8} {'best ms':>9}")
        print("-" * 31)
        for name, fn in STRATEGIES:
            result, queries, ms = measure(conn, fn, rtt_ms)
            if baseline is None:
                baseline = result
            assert result == baseline, f"{name} returned different data"
            print(f"{name:<12} {queries:>8} {ms:>9.2f}")


if __name__ == "__main__":
    main()
