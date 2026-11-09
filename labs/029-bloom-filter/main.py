"""
Lab 029: Bloom filter - filtering out "not there" before asking the store

100,000 existing user IDs go into a Bloom filter. Then 100,000 lookups
arrive, 90% of them for IDs that do not exist (like username checks or
cache-penetration traffic). Without a filter every lookup hits the store.
With a filter only "maybe present" lookups do.

Measures, for several bits-per-key settings:
  - false positive rate, measured vs the formula (1 - e^(-kn/m))^k
  - filter size vs a Python set of the same keys
  - how many of the 100,000 lookups still reach the store
  - false negatives (must be 0)

Then two limits:
  - inserting more keys than planned
  - "deleting" a key by clearing its bits

Standard library only. Run:
    python3 main.py
"""
import gc
import hashlib
import math
import platform
import random
import time
import tracemalloc

N = 100_000            # keys in the store
Q = 100_000            # lookups
ABSENT_RATIO = 0.9     # share of lookups for keys that do not exist
TRIALS = 50_000        # extra absent keys only for measuring false positives
REPEAT = 5
random.seed(29)


class BloomFilter:
    def __init__(self, n_expected, bits_per_key):
        self.m = n_expected * bits_per_key
        self.k = max(1, round(bits_per_key * math.log(2)))
        self.bits = bytearray((self.m + 7) // 8)

    def _positions(self, key):
        # double hashing: h1 + i*h2 from one 128-bit blake2b digest
        d = hashlib.blake2b(key.encode(), digest_size=16).digest()
        h1 = int.from_bytes(d[:8], "little")
        h2 = int.from_bytes(d[8:], "little") | 1
        m = self.m
        return [(h1 + i * h2) % m for i in range(self.k)]

    def add(self, key):
        bits = self.bits
        for p in self._positions(key):
            bits[p >> 3] |= 1 << (p & 7)

    def __contains__(self, key):
        bits = self.bits
        for p in self._positions(key):
            if not bits[p >> 3] & (1 << (p & 7)):
                return False
        return True


def theory_fpr(n, m, k):
    return (1 - math.exp(-k * n / m)) ** k


def make_ids(count, prefix):
    return [f"{prefix}{random.getrandbits(48):012x}" for _ in range(count)]


def set_bytes(keys):
    tracemalloc.start()
    s = set(k for k in keys)  # strings already exist: count only the set table
    size, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return s, size


def best_ms(fn):
    best = float("inf")
    for _ in range(REPEAT):
        gc.collect()
        t = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t)
    return best * 1000


def main():
    present = make_ids(N, "u")
    absent = make_ids(TRIALS, "x")          # never inserted (different prefix)
    n_absent_q = int(Q * ABSENT_RATIO)
    queries = [random.choice(present) for _ in range(Q - n_absent_q)] + make_ids(n_absent_q, "y")
    random.shuffle(queries)
    present_set, set_size = set_bytes(present)

    print(f"Python {platform.python_version()}, keys {N:,}, lookups {Q:,} "
          f"({ABSENT_RATIO:.0%} absent), best of {REPEAT}\n")
    print(f"Python set (table only, strings not counted): {set_size / 1e6:.2f}MB\n")

    print("1) bits per key 별 오탐률과 저장소까지 가는 조회 수")
    print(f"{'bits/key':>8}{'k':>3}{'filter KB':>11}{'FPR 측정':>10}{'FPR 이론':>10}"
          f"{'store hits':>12}{'false neg':>10}")
    results = {}
    for bpk in (4, 8, 10, 16):
        bf = BloomFilter(N, bpk)
        for key in present:
            bf.add(key)
        false_neg = sum(1 for key in present if key not in bf)
        assert false_neg == 0
        fp = sum(1 for key in absent if key in bf)
        fpr = fp / TRIALS
        hits = sum(1 for q in queries if q in bf)
        real = sum(1 for q in queries if q in present_set)
        results[bpk] = (bf, hits, real)
        print(f"{bpk:>8}{bf.k:>3}{len(bf.bits) / 1000:>11.1f}{fpr:>10.2%}"
              f"{theory_fpr(N, bf.m, bf.k):>10.2%}{hits:>12,}{false_neg:>10}")
    real = results[10][2]
    print(f"   필터 없음: 저장소 조회 {Q:,}건, 실제로 있는 키 {real:,}건\n")

    print("2) 조회 10만 건 시간 (Python 구현)")
    bf10 = results[10][0]
    t_set = best_ms(lambda: [q in present_set for q in queries])
    t_bf = best_ms(lambda: [q in bf10 for q in queries])
    print(f"   set {t_set:.1f}ms, bloom(10 bits/key) {t_bf:.1f}ms\n")

    print("3) 예상보다 많이 넣으면 (10 bits/key로 10만 건 기준 크기를 잡고)")
    print(f"{'inserted':>10}{'FPR 측정':>10}{'FPR 이론':>10}")
    extra = make_ids(3 * N, "v")
    for total in (N, 2 * N, 3 * N, 4 * N):
        bf = BloomFilter(N, 10)
        for key in present:
            bf.add(key)
        for key in extra[: total - N]:
            bf.add(key)
        fpr = sum(1 for key in absent if key in bf) / TRIALS
        print(f"{total:>10,}{fpr:>10.2%}{theory_fpr(total, bf.m, bf.k):>10.2%}")

    print("\n4) 키 100개를 지우려고 그 키들의 비트를 0으로 되돌리면 (10 bits/key)")
    bf = BloomFilter(N, 10)
    for key in present:
        bf.add(key)
    removed = present[:100]
    for key in removed:
        for p in bf._positions(key):
            bf.bits[p >> 3] &= ~(1 << (p & 7)) & 0xFF
    still = present[100:]
    lost = sum(1 for key in still if key not in bf)
    print(f"   100개 키 비트 제거 → 남은 {len(still):,}개 중 '없음'으로 잘못 나온 키 {lost:,}개 (false negative)")


if __name__ == "__main__":
    main()
