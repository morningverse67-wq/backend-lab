"""Retry storms after an outage: fixed delay vs exponential backoff vs jitter.

Deterministic fake-clock simulation; Python standard library only.
"""
from pathlib import Path
from statistics import median_low
import argparse
import heapq
import platform
import random
from xml.sax.saxutils import escape

CLIENTS = 100
OUTAGE_UNTIL_MS = 1000   # every attempt before this fails
SLOT_MS = 100            # server capacity is counted per 100 ms slot
CAPACITY = 10            # successful attempts per slot after recovery
BASE_MS = 100
CAP_MS = 3200
MAX_ATTEMPTS = 20        # first try + 19 retries
SEEDS = range(1, 101)


def fixed_delay(retry, rng):
    return BASE_MS


def exponential(retry, rng):
    return min(CAP_MS, BASE_MS * 2 ** retry)


def full_jitter(retry, rng):
    return rng.randint(0, exponential(retry, rng))


def equal_jitter(retry, rng):
    ceiling = exponential(retry, rng)
    return ceiling // 2 + rng.randint(0, ceiling // 2)


POLICIES = (
    ("fixed 100ms", fixed_delay),
    ("exponential", exponential),
    ("full jitter", full_jitter),
    ("equal jitter", equal_jitter),
)


def simulate(delay, seed=1, clients=CLIENTS, outage_until=OUTAGE_UNTIL_MS,
             capacity=CAPACITY, max_attempts=MAX_ATTEMPTS):
    """All clients send at 0 ms. Same-time attempts run in client order."""
    for value in (clients, capacity, max_attempts):
        if type(value) is not int or value <= 0:
            raise ValueError("clients, capacity and max_attempts must be positive integers")
    if type(outage_until) is not int or outage_until < 0:
        raise ValueError("outage_until must be a non-negative integer")
    rng = random.Random(seed)
    events = [(0, client, 1) for client in range(clients)]
    heapq.heapify(events)
    load, served, done = {}, {}, {}
    gave_up = 0
    while events:
        now, client, attempt = heapq.heappop(events)
        slot = now // SLOT_MS
        load[slot] = load.get(slot, 0) + 1
        if now >= outage_until and served.get(slot, 0) < capacity:
            served[slot] = served.get(slot, 0) + 1
            done[client] = now
            continue
        if attempt == max_attempts:
            gave_up += 1
            continue
        wait = delay(attempt - 1, rng)
        if type(wait) is not int or wait < 0:
            raise ValueError("delay must return a non-negative integer")
        heapq.heappush(events, (now + wait, client, attempt + 1))
    finish = sorted(done.values())
    return {
        "succeeded": len(done),
        "gave_up": gave_up,
        "attempts": sum(load.values()),
        # Peak load the recovered server sees; slots before recovery fail anyway.
        "peak_slot": max((n for slot, n in load.items() if slot * SLOT_MS >= outage_until), default=0),
        # Attempts the recovered server had to turn away for lack of capacity.
        "overload_rejects": sum(n for slot, n in load.items() if slot * SLOT_MS >= outage_until) - len(done),
        "p50_ms": finish[(len(finish) - 1) // 2] if finish else None,
        "last_ms": finish[-1] if finish else None,
        "served": served,
    }


def regression_tests():
    checks = 0

    def check(condition):
        nonlocal checks
        assert condition
        checks += 1

    rng = random.Random(0)
    check([exponential(n, rng) for n in range(7)] == [100, 200, 400, 800, 1600, 3200, 3200])
    check(all(0 <= full_jitter(n, rng) <= exponential(n, rng) for n in range(12) for _ in range(500)))
    check(all(exponential(n, rng) // 2 <= equal_jitter(n, rng) <= exponential(n, rng)
              for n in range(12) for _ in range(500)))
    # Hand-computed: fixed retries every 100 ms, 10 succeed per slot from 1000 ms.
    fixed = simulate(fixed_delay)
    check((fixed["succeeded"], fixed["attempts"], fixed["peak_slot"], fixed["last_ms"]) == (100, 1550, 100, 1900))
    check(fixed["overload_rejects"] == 450 and fixed["p50_ms"] == 1400)
    # Hand-computed: synchronized waves at 1500, 3100, 6300, ... 28700 ms.
    expo = simulate(exponential)
    check((expo["succeeded"], expo["attempts"], expo["peak_slot"], expo["last_ms"]) == (100, 950, 100, 28700))
    check(expo["overload_rejects"] == 450 and expo["p50_ms"] == 12700)
    check(simulate(exponential, max_attempts=4)["succeeded"] == 0)
    check(simulate(exponential, max_attempts=5)["succeeded"] == 10)
    check(simulate(fixed_delay, outage_until=0, capacity=100)["attempts"] == 100)
    for _, policy in POLICIES:
        for seed in (1, 2, 3):
            result = simulate(policy, seed)
            check(result["succeeded"] + result["gave_up"] == CLIENTS)
            check(all(count <= CAPACITY for count in result["served"].values()))
            check(all(slot * SLOT_MS >= OUTAGE_UNTIL_MS for slot in result["served"]))
        check(simulate(policy, 9) == simulate(policy, 9))
    for kwargs in ({"clients": 0}, {"capacity": 0}, {"max_attempts": 0}, {"outage_until": -1}, {"clients": 1.5}):
        try:
            simulate(fixed_delay, **kwargs)
        except ValueError:
            check(True)
        else:
            check(False)
    print(f"regression_checks={checks} PASS")


def summarize(values):
    return median_low(values), min(values), max(values)


def experiment():
    print(f"clients={CLIENTS} outage_until_ms={OUTAGE_UNTIL_MS} capacity={CAPACITY}/{SLOT_MS}ms "
          f"base_ms={BASE_MS} cap_ms={CAP_MS} max_attempts={MAX_ATTEMPTS} seeds={SEEDS.start}..{SEEDS.stop - 1}")
    print("policy succeeded attempts overload_rejects peak_attempts_per_100ms_after_recovery p50_done_ms last_done_ms  (median_low [min..max] over seeds)")
    rows = []
    for name, policy in POLICIES:
        runs = [simulate(policy, seed) for seed in SEEDS]
        row = [name]
        for key in ("succeeded", "attempts", "overload_rejects", "peak_slot", "p50_ms", "last_ms"):
            row.append(summarize([run[key] for run in runs]))
        rows.append(row)
        print(name, *(f"{m:g}[{lo}..{hi}]" for m, lo, hi in row[1:]))
    return rows


def write_svg(rows):
    width, height = 1000, 430
    left, top, bar_h, gap = 190, 110, 34, 22
    max_attempts = max(row[2][0] for row in rows)
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
           '<title id="title">Retry after outage: total attempts and peak load per 100 ms</title>',
           '<desc id="desc">100 clients, outage until 1000 ms, capacity 10 per 100 ms. Low medians over 100 seeds. Simulated counts, not latency.</desc>',
           f'<rect width="{width}" height="{height}" fill="#f8fafc"/>',
           '<g font-family="sans-serif" fill="#0f172a">',
           '<text x="28" y="42" font-size="26" font-weight="bold">Retry after a 1 s outage / 100 clients</text>',
           '<text x="28" y="74" font-size="16">Bar: total attempts (low median of 100 seeds). Right: rejected after recovery, peak per 100 ms, last success</text>']
    for i, row in enumerate(rows):
        y = top + i * (bar_h + gap)
        attempts, rejects, peak, last = row[2][0], row[3][0], row[4][0], row[6][0]
        w = attempts / max_attempts * 380
        svg.append(f'<text x="28" y="{y + 23}" font-size="17">{escape(row[0])}</text>')
        svg.append(f'<rect x="{left}" y="{y}" width="{w:.1f}" height="{bar_h}" fill="#3b82f6"/>')
        svg.append(f'<text x="{left + w + 8:.1f}" y="{y + 23}" font-size="16">{attempts}</text>')
        svg.append(f'<text x="660" y="{y + 23}" font-size="16">rejected {rejects} | peak {peak} | last {last} ms</text>')
    svg.extend([f'<text x="28" y="{height - 70}" font-size="15">Server: every attempt before 1000 ms fails; after that 10 succeed per 100 ms slot, the rest are rejected.</text>',
                f'<text x="28" y="{height - 44}" font-size="15">Base 100 ms, cap 3200 ms, max 20 attempts. Deterministic simulation: no network, threads or real latency measured.</text>',
                '</g></svg>'])
    Path(__file__).with_name("result.svg").write_text("\n".join(svg) + "\n", encoding="utf-8")
    print("wrote result.svg")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-svg", action="store_true", help="regenerate result.svg from checked results")
    args = parser.parse_args()
    if not __debug__:
        raise SystemExit("Run without -O: assertions are required")
    print(f"Python {platform.python_version()} | {platform.system()} {platform.release()} | {platform.machine()}")
    regression_tests()
    rows = experiment()
    if args.write_svg:
        write_svg(rows)


if __name__ == "__main__":
    main()
