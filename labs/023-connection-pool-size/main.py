"""Connection pool size versus waiting time in front of a 4-core database.

Deterministic event simulation with a fake clock; Python standard library only.
"""
from collections import deque
from math import ceil
from pathlib import Path
from statistics import median_low
import argparse
import heapq
import platform
import random
from xml.sax.saxutils import escape

RATE_PER_S = 800          # Poisson arrivals, 800 requests per second
DURATION_MS = 10_000      # arrivals during 10 s (about 8,000 requests)
MEAN_WORK_MS = 4.0        # one query needs 4 ms of DB CPU on average (exponential)
CORES = 4                 # DB CPU cores: 800/s x 4 ms / 4 cores = 80 % busy
ACQUIRE_TIMEOUT_MS = 1000 # give up when no connection is free within 1 s
POOL_SIZES = (2, 4, 8, 16, 32, 64)
HOLD_MS = 20              # scenario B: connection also held 20 ms for non-DB work
CONTENTION = 0.02         # assumption: each query above the core count slows all by 2 %
SEEDS = range(1, 21)


def requests_for(seed, rate_per_s=RATE_PER_S, duration_ms=DURATION_MS):
    """Same arrival times and query work for every pool size (common random numbers)."""
    rng = random.Random(seed)
    arrivals, works, t = [], [], 0.0
    while True:
        t += rng.expovariate(rate_per_s / 1000)
        if t >= duration_ms:
            return arrivals, works
        arrivals.append(t)
        works.append(rng.expovariate(1 / MEAN_WORK_MS))


def db_rate(active, cores, contention):
    """Work done per ms by each active query under processor sharing."""
    if active == 0:
        return 0.0
    return min(1.0, cores / active) / (1 + contention * max(0, active - cores))


def simulate(pool, arrivals, works, hold_ms=0.0, cores=CORES, contention=0.0,
             timeout_ms=ACQUIRE_TIMEOUT_MS):
    """Each request waits for a free connection (FIFO), runs its query, holds the
    connection hold_ms more, then releases it. The DB shares its cores among all
    running queries. At equal times: query finishes, then releases, then
    acquire timeouts, then new arrivals.
    """
    if type(pool) is not int or pool <= 0:
        raise ValueError("pool must be a positive integer")
    if type(cores) is not int or cores <= 0:
        raise ValueError("cores must be a positive integer")
    if len(arrivals) != len(works) or any(w <= 0 for w in works):
        raise ValueError("every request needs a positive amount of work")
    if any(b < a for a, b in zip(arrivals, arrivals[1:])):
        raise ValueError("arrivals must be sorted")
    if hold_ms < 0 or contention < 0 or timeout_ms <= 0:
        raise ValueError("hold_ms, contention must be >= 0 and timeout_ms > 0")

    n = len(arrivals)
    t = vwork = 0.0                 # vwork: work each running query has received so far
    free, nxt = pool, 0
    running = []                    # heap of (vwork at finish, request)
    holding = []                    # heap of (release time, request)
    waiting = deque()               # FIFO of requests waiting for a connection
    acquired = [None] * n
    db_done = [None] * n
    released = [None] * n
    timed_out = 0
    peak_running = peak_waiting = 0

    def acquire(i):
        nonlocal free, peak_running
        acquired[i] = t
        heapq.heappush(running, (vwork + works[i], i))
        peak_running = max(peak_running, len(running))

    def release(i):
        nonlocal free
        released[i] = t
        if waiting:
            acquire(waiting.popleft())
        else:
            free += 1

    while nxt < n or running or holding or waiting:
        rate = db_rate(len(running), cores, contention)
        cands = []
        if running:
            cands.append((t + (running[0][0] - vwork) / rate, 0))
        if holding:
            cands.append((holding[0][0], 1))
        if waiting:
            cands.append((arrivals[waiting[0]] + timeout_ms, 2))
        if nxt < n:
            cands.append((arrivals[nxt], 3))
        when, kind = min(cands)
        vwork += rate * (when - t)
        t = when
        if kind == 0:
            _, i = heapq.heappop(running)
            db_done[i] = t
            if hold_ms:
                heapq.heappush(holding, (t + hold_ms, i))
            else:
                release(i)
        elif kind == 1:
            _, i = heapq.heappop(holding)
            release(i)
        elif kind == 2:
            waiting.popleft()
            timed_out += 1
        else:
            i, nxt = nxt, nxt + 1
            if free:
                free -= 1
                acquire(i)
            else:
                waiting.append(i)
                peak_waiting = max(peak_waiting, len(waiting))

    done = [i for i in range(n) if released[i] is not None]
    total = [released[i] - arrivals[i] for i in done]
    pool_wait = [acquired[i] - arrivals[i] for i in done]
    in_db = [db_done[i] - acquired[i] for i in done]
    held = sum(released[i] - acquired[i] for i in done)
    return dict(requests=n, completed=len(done), timed_out=timed_out,
                total=total, pool_wait=pool_wait, in_db=in_db, held_ms=held,
                peak_running=peak_running, peak_waiting=peak_waiting,
                end_ms=t, free_at_end=free)


def pct(values, p):
    """Nearest-rank percentile."""
    s = sorted(values)
    return s[max(0, ceil(p / 100 * len(s)) - 1)]


def summary(r):
    return dict(p50=pct(r["total"], 50), p99=pct(r["total"], 99),
                wait_p99=pct(r["pool_wait"], 99), db_p50=pct(r["in_db"], 50),
                db_p99=pct(r["in_db"], 99), timed_out=r["timed_out"],
                peak_running=r["peak_running"],
                conns_in_use=r["held_ms"] / r["end_ms"],
                hold_mean=r["held_ms"] / r["completed"])


def regression_tests():
    checks = 0

    def check(condition):
        nonlocal checks
        assert condition
        checks += 1

    def close(a, b):
        return abs(a - b) < 1e-9

    # One request, one connection: latency = its 4 ms of work.
    r = simulate(1, [0.0], [4.0])
    check(r["completed"] == 1 and close(r["total"][0], 4.0) and r["free_at_end"] == 1)
    # Pool 1, two requests at 0: the second waits 4 ms in the pool, done at 8 ms.
    r = simulate(1, [0.0, 0.0], [4.0, 4.0])
    check([round(x, 9) for x in r["pool_wait"]] == [0, 4] and close(r["total"][1], 8.0))
    # Pool 2 on 1 core: both run at half speed and finish together at 8 ms.
    r = simulate(2, [0.0, 0.0], [4.0, 4.0], cores=1)
    check(r["pool_wait"] == [0, 0] and all(close(x, 8.0) for x in r["in_db"]))
    # Same, with 50 % contention per extra query: each runs at 1/3 speed -> 12 ms.
    r = simulate(2, [0.0, 0.0], [4.0, 4.0], cores=1, contention=0.5)
    check(all(close(x, 12.0) for x in r["in_db"]) and r["peak_running"] == 2)
    # Short query finishes first under processor sharing: 2 ms and 6 ms work on 1 core.
    r = simulate(2, [0.0, 0.0], [2.0, 6.0], cores=1)
    check(close(r["in_db"][0], 4.0) and close(r["in_db"][1], 8.0))
    # Holding the connection 20 ms after the query: second request done at 4+20+4+20.
    r = simulate(1, [0.0, 0.0], [4.0, 4.0], hold_ms=20.0)
    check(close(r["total"][1], 48.0) and close(r["pool_wait"][1], 24.0))
    # Acquire timeout: the second request gives up at 1000 ms, the first still completes.
    r = simulate(1, [0.0, 0.0], [2000.0, 1.0])
    check((r["completed"], r["timed_out"], r["peak_waiting"]) == (1, 1, 1))
    # A request that gets the connection exactly at its deadline is served (release first).
    r = simulate(1, [0.0, 0.0], [1000.0, 1.0])
    check((r["completed"], r["timed_out"]) == (2, 0))
    # Pool never lets more queries into the DB than its size.
    arrivals, works = requests_for(7, duration_ms=2000)
    for pool in (1, 3, 8):
        for hold in (0.0, HOLD_MS):
            r = simulate(pool, arrivals, works, hold_ms=hold)
            check(r["peak_running"] <= pool)
            check(r["completed"] + r["timed_out"] == r["requests"] and r["free_at_end"] == pool)
            check(all(w <= ACQUIRE_TIMEOUT_MS + 1e-9 for w in r["pool_wait"]))
    # Contention only applies above the core count, so pool <= cores is unaffected.
    check(simulate(4, arrivals, works) == simulate(4, arrivals, works, contention=CONTENTION))
    # DB capacity with 2 % contention: 16 running queries still do 3.23 cores of work,
    # 17 do 3.17 -- less than the 3.2 cores that 800/s x 4 ms needs.
    need = RATE_PER_S / 1000 * MEAN_WORK_MS
    check(16 * db_rate(16, CORES, CONTENTION) > need > 17 * db_rate(17, CORES, CONTENTION))
    check(simulate(8, arrivals, works) == simulate(8, arrivals, works))
    check(requests_for(3) == requests_for(3) and requests_for(3) != requests_for(4))
    for kwargs in ({"pool": 0}, {"pool": 1.5}, {"cores": 0}, {"hold_ms": -1},
                   {"contention": -0.1}, {"timeout_ms": 0}):
        args = dict(pool=1, arrivals=[0.0], works=[1.0]) | kwargs
        try:
            simulate(**args)
        except ValueError:
            check(True)
        else:
            check(False)
    for bad in (([1.0, 0.0], [1.0, 1.0]), ([0.0], [0.0]), ([0.0], [1.0, 1.0])):
        try:
            simulate(1, *bad)
        except ValueError:
            check(True)
        else:
            check(False)
    print(f"regression_checks={checks} PASS")


SCENARIOS = [
    ("A query only", 0.0, 0.0),
    ("A + contention 2%", 0.0, CONTENTION),
    ("B hold 20ms", HOLD_MS, 0.0),
]
KEYS = ("p50", "p99", "wait_p99", "db_p99", "timed_out", "peak_running", "conns_in_use", "hold_mean")


def experiment():
    print(f"arrivals={RATE_PER_S}/s poisson for {DURATION_MS}ms, query work exp mean {MEAN_WORK_MS}ms, "
          f"db_cores={CORES}, acquire_timeout={ACQUIRE_TIMEOUT_MS}ms, seeds={SEEDS.start}..{SEEDS.stop - 1}")
    print("values: median_low over seeds [min..max]; latency in simulated ms (nearest-rank percentiles)")
    streams = [requests_for(seed) for seed in SEEDS]
    print("requests per seed:", median_low(len(a) for a, _ in streams),
          f"[{min(len(a) for a, _ in streams)}..{max(len(a) for a, _ in streams)}]")
    table = {}
    for name, hold, contention in SCENARIOS:
        print(f"[{name}] pool " + " ".join(KEYS))
        for pool in POOL_SIZES:
            runs = [summary(simulate(pool, a, w, hold_ms=hold, contention=contention)) for a, w in streams]
            row = {k: (median_low([r[k] for r in runs]), min(r[k] for r in runs), max(r[k] for r in runs))
                   for k in KEYS}
            table[name, pool] = row
            print(pool, *(f"{m:.2f}[{lo:.2f}..{hi:.2f}]" if isinstance(m, float)
                          else f"{m}[{lo}..{hi}]" for m, lo, hi in row.values()))
    return table


def write_svg(table):
    width, height = 1000, 560
    left, right, top, bottom = 110, 930, 150, 460
    colors = {"A query only": "#2563eb", "A + contention 2%": "#dc2626", "B hold 20ms": "#16a34a"}
    labels = {"A query only": "A: query only", "A + contention 2%": "A + 2% contention per extra query",
              "B hold 20ms": "B: connection also held 20 ms"}
    lo_ms, hi_ms = 10, 2000
    from math import log10

    def x(i):
        return left + i * (right - left) / (len(POOL_SIZES) - 1)

    def y(ms):
        return bottom - (log10(ms) - log10(lo_ms)) / (log10(hi_ms) - log10(lo_ms)) * (bottom - top)

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
           '<title id="title">p99 latency by connection pool size in front of a 4-core database</title>',
           f'<desc id="desc">800 requests per second, 4 ms query on average, acquire timeout 1000 ms. p99 of completed requests, low median of {len(SEEDS)} seeds. Simulated time, not measured latency.</desc>',
           f'<rect width="{width}" height="{height}" fill="#f8fafc"/>',
           '<g font-family="sans-serif" fill="#0f172a">',
           '<text x="28" y="42" font-size="26" font-weight="bold">Connection pool size vs p99 latency / 4-core DB, 800 req/s</text>',
           f'<text x="28" y="74" font-size="16">p99 of completed requests (log scale), low median of {len(SEEDS)} seeds. Numbers in brackets: acquire timeouts.</text>']
    for ms in (10, 30, 100, 300, 1000):
        svg.append(f'<line x1="{left}" y1="{y(ms):.1f}" x2="{right}" y2="{y(ms):.1f}" stroke="#cbd5e1"/>')
        svg.append(f'<text x="{left - 10}" y="{y(ms) + 5:.1f}" font-size="14" text-anchor="end">{ms} ms</text>')
    for i, pool in enumerate(POOL_SIZES):
        svg.append(f'<text x="{x(i):.1f}" y="{bottom + 26}" font-size="15" text-anchor="middle">{pool}</text>')
    svg.append(f'<text x="{(left + right) / 2:.1f}" y="{bottom + 52}" font-size="15" text-anchor="middle">pool size (DB has 4 cores)</text>')
    for k, (name, _, _) in enumerate(SCENARIOS):
        pts = [(x(i), y(table[name, pool]["p99"][0])) for i, pool in enumerate(POOL_SIZES)]
        svg.append(f'<polyline fill="none" stroke="{colors[name]}" stroke-width="3" points="' +
                   " ".join(f"{px:.1f},{py:.1f}" for px, py in pts) + '"/>')
        for (px, py), pool in zip(pts, POOL_SIZES):
            row = table[name, pool]
            svg.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="5" fill="{colors[name]}"/>')
            if k == 1 and row == table[SCENARIOS[0][0], pool]:
                continue   # same as A (pool <= cores): the blue label covers it
            note = f"{row['p99'][0]:.0f}" + (f" [{row['timed_out'][0]}]" if row["timed_out"][0] else "")
            ty = py + 24 if k == 0 else py - 10
            anchor = "end" if pool == POOL_SIZES[-1] else "start"
            tx = px - 8 if anchor == "end" else px + 8
            svg.append(f'<text x="{tx:.1f}" y="{ty:.1f}" font-size="14" fill="{colors[name]}" text-anchor="{anchor}">{note}</text>')
        lx = 28 + k * 300
        svg.append(f'<rect x="{lx}" y="98" width="16" height="16" fill="{colors[name]}"/>')
        svg.append(f'<text x="{lx + 22}" y="112" font-size="14">{escape(labels[name])}</text>')
    svg.extend([f'<text x="28" y="{height - 22}" font-size="14">Queries share the 4 cores (processor sharing). The 2% contention cost is an assumption, not a measurement.</text>',
                '</g></svg>'])
    Path(__file__).with_name("result.svg").write_text("\n".join(svg) + "\n", encoding="utf-8")
    print("wrote result.svg")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-svg", action="store_true", help="regenerate result.svg from checked results")
    args = parser.parse_args()
    if not __debug__:
        raise SystemExit("Run without -O: assertions are required")
    print(f"Python {platform.python_version()} | {platform.system()} {platform.release()} | {platform.machine()}")
    regression_tests()
    table = experiment()
    if args.write_svg:
        write_svg(table)
