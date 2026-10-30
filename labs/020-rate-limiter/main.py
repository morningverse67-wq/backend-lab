"""Single-key sequential simulation; Python standard library only."""
from collections import deque
from pathlib import Path
import argparse
import platform
from xml.sax.saxutils import escape

LIMIT = 5
WINDOW_MS = 1000


class FakeClock:
    def __init__(self):
        self.now_ms = 0

    def advance_to(self, now_ms):
        if type(now_ms) is not int or now_ms < self.now_ms:
            raise ValueError("clock must advance monotonically in integer milliseconds")
        self.now_ms = now_ms


class Limiter:
    def __init__(self, clock, limit=LIMIT, window_ms=WINDOW_MS):
        if type(limit) is not int or type(window_ms) is not int or min(limit, window_ms) <= 0:
            raise ValueError("limit and window_ms must be positive integers")
        self.clock, self.limit, self.window_ms = clock, limit, window_ms


class FixedWindow(Limiter):
    """Epoch-aligned [kW, (k+1)W); only accepted requests count."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.window_id, self.count = None, 0

    def allow(self):
        window_id = self.clock.now_ms // self.window_ms
        if window_id != self.window_id:
            self.window_id, self.count = window_id, 0
        if self.count == self.limit:
            return False
        self.count += 1
        return True


class SlidingWindowLog(Limiter):
    """Exact accepted timestamps in (now-W, now], not an approximation."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.accepted = deque()

    def allow(self):
        now = self.clock.now_ms
        while self.accepted and self.accepted[0] <= now - self.window_ms:
            self.accepted.popleft()
        if len(self.accepted) == self.limit:
            return False
        self.accepted.append(now)
        return True


class TokenBucket(Limiter):
    """Full initially. Integer credits: request costs W, refill L per ms."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.capacity = self.limit * self.window_ms
        self.credits = self.capacity
        self.last_ms = self.clock.now_ms

    def allow(self):
        now = self.clock.now_ms
        self.credits = min(self.capacity, self.credits + (now - self.last_ms) * self.limit)
        self.last_ms = now
        if self.credits < self.window_ms:
            return False
        self.credits -= self.window_ms
        return True


POLICIES = (FixedWindow, SlidingWindowLog, TokenBucket)
WORKLOADS = {
    "steady": list(range(0, 3000, 100)),
    "boundary": [999] * 5 + [1000] * 5,
    "refill_burst": [0] * 5 + [800] * 5,
    "idle_cap": [0] * 10 + [10000] * 10,
}


def replay(policy, times, limit=LIMIT, window_ms=WINDOW_MS):
    clock = FakeClock()
    limiter = policy(clock, limit, window_ms)
    accepted = []
    for now in times:
        clock.advance_to(now)
        if limiter.allow():
            accepted.append(now)
    return accepted


def max_rolling(accepted, window_ms=WINDOW_MS):
    """Peak accepted count in any (t-W, t], including same-time requests."""
    active = deque()
    maximum = 0
    for now in accepted:
        while active and active[0] <= now - window_ms:
            active.popleft()
        active.append(now)
        maximum = max(maximum, len(active))
    return maximum


def regression_tests():
    checks = 0

    def check(condition):
        nonlocal checks
        assert condition
        checks += 1

    check(replay(FixedWindow, [999] * 6 + [1000] * 6) == [999] * 5 + [1000] * 5)
    check(replay(SlidingWindowLog, [0] * 5 + [999, 1000]) == [0] * 5 + [1000])
    check(replay(SlidingWindowLog, [0] * 5 + [500, 999, 1000]) == [0] * 5 + [1000])
    check(replay(TokenBucket, [0] * 5 + [199, 200, 200]) == [0] * 5 + [200])
    check(replay(TokenBucket, [0] * 5 + list(range(1, 201))) == [0] * 5 + [200])
    check(replay(TokenBucket, [0] * 10 + [10000] * 10) == [0] * 5 + [10000] * 5)
    check(max_rolling([0, 999, 1000]) == 2)
    for policy in POLICIES:
        check(replay(policy, [0] * 6, limit=2, window_ms=7) == [0, 0])
    clock = FakeClock()
    clock.advance_to(2)
    for invalid in (1, 2.5, True):
        try:
            clock.advance_to(invalid)
        except ValueError:
            check(True)
        else:
            check(False)
    for policy in POLICIES:
        for limit, window in ((0, 1000), (5, 0), (1.5, 1000)):
            try:
                policy(FakeClock(), limit, window)
            except ValueError:
                check(True)
            else:
                check(False)
    times = sorted((i * 137) % 10000 for i in range(400))
    accepted = replay(SlidingWindowLog, times)
    # Independent oracle: count all accepted timestamps in every trailing interval.
    check(all(sum(t - WINDOW_MS < a <= t for a in accepted) <= LIMIT for t in times))
    for policy in POLICIES:
        check(replay(policy, times) == replay(policy, times))
    print(f"regression_checks={checks} PASS")


def experiment():
    rows = []
    expected = {
        "steady": ((15, 5), (15, 5), (19, 9)),
        "boundary": ((10, 10), (5, 5), (5, 5)),
        "refill_burst": ((5, 5), (5, 5), (9, 9)),
        "idle_cap": ((10, 5), (10, 5), (10, 5)),
    }
    print(f"limit={LIMIT} window_ms={WINDOW_MS}; token capacity={LIMIT} refill={LIMIT}/s initially_full=True")
    print("workload policy requests allowed denied max_rolling_1000ms")
    for name, times in WORKLOADS.items():
        for policy, target in zip(POLICIES, expected[name]):
            accepted = replay(policy, times)
            count, maximum = len(accepted), max_rolling(accepted)
            assert (count, maximum) == target, (name, policy.__name__, count, maximum)
            row = (name, policy.__name__, len(times), count, len(times) - count, maximum)
            rows.append(row)
            print(*row)
    print("workload_expectations=12 PASS")
    return rows


def write_svg(rows):
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="670" viewBox="0 0 1000 670" role="img" aria-labelledby="title desc">',
           '<title id="title">Rate limiter: accepted counts and boundary bursts</title>',
           '<desc id="desc">Same deterministic input per policy. Limit 5 per 1000 ms, token capacity 5, refill 5 per second. No latency measured.</desc>',
           '<rect width="1000" height="670" fill="#f8fafc"/>',
           '<g font-family="sans-serif" fill="#0f172a">',
           '<text x="28" y="42" font-size="26" font-weight="bold">Rate limiter / deterministic fake clock</text>',
           '<text x="28" y="74" font-size="16">Limit 5 / 1000 ms | bucket capacity 5, refill 5/s, initially full</text>']
    headers = ('Workload', 'Policy', 'Requests', 'Allowed', 'Denied', 'Peak rolling 1s')
    xs = (28, 220, 465, 585, 695, 800)
    for x, label in zip(xs, headers):
        svg.append(f'<text x="{x}" y="120" font-size="16" font-weight="bold">{label}</text>')
    for i, row in enumerate(rows):
        y = 156 + i * 36
        for x, value in zip(xs, row):
            svg.append(f'<text x="{x}" y="{y}" font-size="16">{escape(str(value))}</text>')
    svg.extend(['<text x="28" y="615" font-size="16">Peak counts accepted requests in (t - 1000 ms, t]. Token bucket is not a strict rolling cap.</text>',
                '<text x="28" y="644" font-size="16">Single key, sequential simulation. No network, concurrency, distributed state or latency measured.</text>', '</g></svg>'])
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
