"""
Lab 030: Consistent hashing - how many keys move when a node is added

100,000 cache keys are spread over 10 nodes. Then an 11th node is added
(and, separately, one node is removed). Compares:
  - modulo hashing:      node = hash(key) % N
  - consistent hashing:  hash ring, 1 point per node
  - consistent hashing:  hash ring, 10 / 100 / 1000 virtual nodes per node

Measures:
  - keys that move to a different node (ideal when adding: 1/11 = 9.09%)
  - load balance: max node load / average, and the smallest node
  - lookup time (bisect on the ring vs one modulo)

Standard library only. Run:
    python3 main.py
"""
import bisect
import hashlib
import platform
import statistics
import time

K = 100_000          # keys
N = 10               # nodes before scaling
REPEAT = 5


def h(s):
    return int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest(), "big")


KEYS = [f"user:{i}" for i in range(K)]
KEY_HASH = [h(k) for k in KEYS]


class Modulo:
    def __init__(self, nodes):
        self.nodes = list(nodes)

    def get(self, kh):
        return self.nodes[kh % len(self.nodes)]


class Ring:
    def __init__(self, nodes, vnodes):
        pts = sorted((h(f"{n}#{v}"), n) for n in nodes for v in range(vnodes))
        self.hashes = [p for p, _ in pts]
        self.owners = [n for _, n in pts]

    def get(self, kh):
        i = bisect.bisect(self.hashes, kh)
        return self.owners[i % len(self.owners)]


def assign(router):
    return [router.get(kh) for kh in KEY_HASH]


def balance(owners, nodes):
    cnt = {n: 0 for n in nodes}
    for o in owners:
        cnt[o] += 1
    avg = K / len(nodes)
    return max(cnt.values()) / avg, min(cnt.values()), max(cnt.values()), statistics.pstdev(cnt.values()) / avg


def moved(a, b):
    return sum(1 for x, y in zip(a, b) if x != y)


def lookup_ms(router):
    best = float("inf")
    for _ in range(REPEAT):
        t = time.perf_counter()
        for kh in KEY_HASH:
            router.get(kh)
        best = min(best, time.perf_counter() - t)
    return best * 1000


def main():
    before = [f"node-{i}" for i in range(N)]
    after_add = before + [f"node-{N}"]
    after_remove = [n for n in before if n != "node-3"]

    cases = [("modulo (hash % N)", lambda ns: Modulo(ns))]
    for v in (1, 10, 100, 1000):
        cases.append((f"ring, vnodes={v}", lambda ns, v=v: Ring(ns, v)))

    print(f"keys={K:,}, nodes {N} -> {N + 1} (add) / {N} -> {N - 1} (remove node-3)")
    print(f"ideal moved on add: 1/{N + 1} = {100 / (N + 1):.2f}%, on remove: 1/{N} = {100 / N:.2f}%\n")
    print(f"{'scheme':<20}{'moved(add)':>18}{'moved(remove)':>18}{'max/avg':>9}{'min node':>10}{'max node':>10}{'CV':>8}{'lookup':>10}")
    for name, make in cases:
        r0 = make(before)
        a0 = assign(r0)
        a1 = assign(make(after_add))
        a2 = assign(make(after_remove))
        m1, m2 = moved(a0, a1), moved(a0, a2)
        mx, mn, mxc, cv = balance(a0, before)
        print(f"{name:<20}{m1:>9,} ({100 * m1 / K:5.2f}%){m2:>9,} ({100 * m2 / K:5.2f}%)"
              f"{mx:>9.2f}{mn:>10,}{mxc:>10,}{cv:>8.3f}{lookup_ms(r0):>8.1f}ms")

    # where do moved keys go on ring add? (all should go to the new node)
    r0, r1 = Ring(before, 100), Ring(after_add, 100)
    a0, a1 = assign(r0), assign(r1)
    to_new = sum(1 for x, y in zip(a0, a1) if x != y and y == f"node-{N}")
    print(f"\nring vnodes=100 add: moved keys that went to the new node = {to_new:,} / {moved(a0, a1):,}")
    mo0, mo1 = assign(Modulo(before)), assign(Modulo(after_add))
    to_new_m = sum(1 for x, y in zip(mo0, mo1) if x != y and y == f"node-{N}")
    print(f"modulo add: moved keys that went to the new node = {to_new_m:,} / {moved(mo0, mo1):,}")

    print(f"\nPython {platform.python_version()} / {platform.system()} {platform.machine()}")


if __name__ == "__main__":
    main()
