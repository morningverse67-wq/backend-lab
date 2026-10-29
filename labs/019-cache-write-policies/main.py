"""Deterministic operation counts, not a latency benchmark. Python stdlib only."""
import json
import sqlite3


class Store:
    def __init__(self, policy):
        self.policy = policy
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, value INTEGER)")
        self.db.executemany("INSERT INTO items VALUES (?, 0)", [(i,) for i in range(10)])
        self.db.commit()
        self.cache = {i: 0 for i in range(10)}
        self.pending = {}
        self.writes = self.reads = self.acked = self.since_flush = 0

    def persist(self, key, value):
        self.db.execute("UPDATE items SET value=? WHERE id=?", (value, key))
        self.writes += 1

    def write(self, key, value):
        if self.policy == "write-behind":
            self.cache[key] = value
            self.pending[key] = value
            self.since_flush += 1
        else:
            self.persist(key, value)
            self.db.commit()
            if self.policy == "cache-aside":
                self.cache.pop(key, None)
            else:
                self.cache[key] = value
        self.acked += 1

    def read(self, key):
        if key not in self.cache:
            self.reads += 1
            self.cache[key] = self.db.execute("SELECT value FROM items WHERE id=?", (key,)).fetchone()[0]
        return self.cache[key]

    def flush(self):
        for key, value in self.pending.items():
            self.persist(key, value)
        self.db.commit()
        self.pending.clear()
        self.since_flush = 0

    def values(self):
        return dict(self.db.execute("SELECT id, value FROM items"))

    def crash_cache_process(self):
        self.cache.clear()
        self.pending.clear()


def run(policy, count, crash=False):
    store = Store(policy)
    latest = {i: 0 for i in range(10)}
    for seq in range(1, count + 1):
        key = (seq - 1) % 10
        store.write(key, seq)
        latest[key] = seq
        assert store.read(key) == seq
        if policy == "write-behind" and seq % 20 == 0:
            store.flush()
    pending_keys = len(store.pending)
    unflushed_acks = store.since_flush
    before = sum(store.values()[k] != v for k, v in latest.items())
    if crash:
        store.crash_cache_process()
    else:
        store.flush()
    after = sum(store.values()[k] != v for k, v in latest.items())
    result = dict(policy=policy, acknowledged=store.acked, db_updates=store.writes,
                  db_read_misses=store.reads, pending_keys_before_end=pending_keys,
                  unflushed_acknowledged_writes=unflushed_acks,
                  db_keys_behind_before_end=before, db_keys_behind_after_end=after)
    assert after == (10 if crash and policy == "write-behind" else 0)
    store.db.close()
    return result


def stale_fill_race():
    store = Store("cache-aside")
    store.cache.clear()
    old = store.db.execute("SELECT value FROM items WHERE id=0").fetchone()[0]
    store.write(0, 1)
    store.cache[0] = old
    stale = store.read(0)
    assert stale == 0 and store.values()[0] == 1
    store.db.close()
    return dict(cached_value=stale, db_value=1)


def main():
    policies = ("cache-aside", "write-through", "write-behind")
    healthy = [run(p, 100) for p in policies]
    crash = [run(p, 95, crash=True) for p in policies]
    assert [r["db_updates"] for r in healthy] == [100, 100, 50]
    assert [r["db_read_misses"] for r in healthy] == [100, 0, 0]
    assert crash[2]["unflushed_acknowledged_writes"] == 15
    assert crash[2]["db_updates"] == 40
    print(json.dumps(dict(healthy=healthy, crash_at_95=crash,
                          cache_aside_stale_fill=stale_fill_race()), indent=2))


if __name__ == "__main__":
    main()
