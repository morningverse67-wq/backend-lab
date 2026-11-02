"""Circuit breaker state transitions during a downstream outage.

Deterministic fake-clock simulation; Python standard library only.
"""
from collections import deque
from pathlib import Path
from statistics import median_low
import argparse
import heapq
import platform
from xml.sax.saxutils import escape

INTERVAL_MS = 10          # one request every 10 ms (100 per second)
REQUESTS = 2000           # 0 .. 19,990 ms
OUTAGE_START_MS = 1000
OUTAGE_END_MS = 6000      # main scenario: 5 s outage
OK_LATENCY_MS = 20        # healthy downstream answers in 20 ms
TIMEOUT_MS = 1000         # during the outage a call hangs until the client timeout
WINDOW = 20               # breaker looks at the last 20 completed calls
MIN_CALLS = 10            # ... once it has at least 10 of them
FAILURE_RATE = 0.5        # open at >= 50 % failures
HALF_OPEN_CALLS = 3       # trial calls allowed in HALF_OPEN; all must succeed
OPEN_DURATIONS = (500, 2000, 5000)
SWEEP_ENDS = range(2000, 11001, 10)   # outage end 2,000 .. 11,000 ms (901 runs)


def simulate(open_ms=None, outage=(OUTAGE_START_MS, OUTAGE_END_MS), fail_call=None,
             requests=REQUESTS):
    """open_ms=None means no breaker. fail_call(i) marks extra failing calls by request index.

    Completions at time t are processed before the request that arrives at t.
    OPEN -> HALF_OPEN happens on the first request at or after the open timer.
    Results of calls sent before the latest state change are not counted by the new state.
    Calls still in flight after the last request are counted but no longer move the breaker.
    """
    if open_ms is not None and (type(open_ms) is not int or open_ms <= 0):
        raise ValueError("open_ms must be a positive integer or None")
    start, end = outage
    if type(start) is not int or type(end) is not int or not 0 <= start <= end:
        raise ValueError("outage must be (start, end) with 0 <= start <= end")
    if type(requests) is not int or requests <= 0:
        raise ValueError("requests must be a positive integer")

    state, generation, open_until = "CLOSED", 0, None
    window = deque(maxlen=WINDOW)
    trials_sent = trials_ok = 0
    inflight = []      # heap of (done_ms, request, generation, ok, trial)
    log = []
    stats = dict(calls=0, succeeded=0, timed_out=0, rejected=0, rejected_after_recovery=0,
                 calls_in_outage=0, timeout_wait_ms=0, peak_in_flight=0)

    def move(to, now):
        nonlocal state, generation, open_until, trials_sent, trials_ok
        state, generation = to, generation + 1
        window.clear()
        trials_sent = trials_ok = 0
        open_until = now + open_ms if to == "OPEN" else None
        log.append((now, to))

    def complete(now, gen, ok, trial, feed=True):
        nonlocal trials_ok
        if ok:
            stats["succeeded"] += 1
        else:
            stats["timed_out"] += 1
            stats["timeout_wait_ms"] += TIMEOUT_MS
        if open_ms is None or not feed or gen != generation:
            return
        if state == "CLOSED":
            window.append(ok)
            failures = window.count(False)
            if len(window) >= MIN_CALLS and failures >= FAILURE_RATE * len(window):
                move("OPEN", now)
        elif state == "HALF_OPEN" and trial:
            if not ok:
                move("OPEN", now)
            else:
                trials_ok += 1
                if trials_ok == HALF_OPEN_CALLS:
                    move("CLOSED", now)

    for i in range(requests):
        now = i * INTERVAL_MS
        while inflight and inflight[0][0] <= now:
            done, _, gen, ok, trial = heapq.heappop(inflight)
            complete(done, gen, ok, trial)
        trial = False
        if open_ms is not None:
            if state == "OPEN" and now >= open_until:
                move("HALF_OPEN", now)
            if state == "OPEN" or (state == "HALF_OPEN" and trials_sent == HALF_OPEN_CALLS):
                stats["rejected"] += 1
                if now >= end:
                    stats["rejected_after_recovery"] += 1
                continue
            if state == "HALF_OPEN":
                trials_sent += 1
                trial = True
        in_outage = start <= now < end
        ok = not in_outage and not (fail_call and fail_call(i))
        stats["calls"] += 1
        stats["calls_in_outage"] += in_outage
        heapq.heappush(inflight, (now + (OK_LATENCY_MS if ok else TIMEOUT_MS), i, generation, ok, trial))
        stats["peak_in_flight"] = max(stats["peak_in_flight"], len(inflight))
    while inflight:
        done, _, gen, ok, trial = heapq.heappop(inflight)
        complete(done, gen, ok, trial, feed=False)

    closes = [t for t, to in log if to == "CLOSED" and t >= end]
    stats["first_open_ms"] = next((t for t, to in log if to == "OPEN"), None)
    stats["closed_at_ms"] = closes[0] if open_ms is not None and closes else None
    stats["recovery_delay_ms"] = (0 if open_ms is None or not log
                                  else closes[0] - end if closes else None)
    stats["log"] = log
    return stats


POLICIES = [("no breaker", None)] + [(f"breaker open {ms}ms", ms) for ms in OPEN_DURATIONS]


def regression_tests():
    checks = 0

    def check(condition):
        nonlocal checks
        assert condition
        checks += 1

    # Hand-computed, no breaker: 500 calls in the outage all wait 1000 ms.
    nb = simulate(None)
    check((nb["calls"], nb["succeeded"], nb["timed_out"], nb["rejected"]) == (2000, 1500, 500, 0))
    check((nb["timeout_wait_ms"], nb["peak_in_flight"], nb["log"]) == (500_000, 100, []))
    # Hand-computed, open 2000 ms: the 10th failure (call at 1090 ms) lands at 2090 ms.
    b2 = simulate(2000)
    check(b2["log"] == [(2090, "OPEN"), (4090, "HALF_OPEN"), (5090, "OPEN"),
                        (7090, "HALF_OPEN"), (7130, "CLOSED")])
    check((b2["calls_in_outage"], b2["timed_out"], b2["timeout_wait_ms"]) == (112, 112, 112_000))
    check((b2["rejected"], b2["rejected_after_recovery"], b2["recovery_delay_ms"]) == (498, 110, 1130))
    check(b2["succeeded"] == 1390 and b2["calls"] + b2["rejected"] == REQUESTS)
    # The first wave cannot be stopped: failures are only known after the 1000 ms timeout.
    check(b2["peak_in_flight"] == nb["peak_in_flight"] == 100)
    # Short open timer probes more often but still waits for the same trial timeout here.
    b05 = simulate(500)
    check([to for _, to in b05["log"]].count("HALF_OPEN") == 4 and b05["closed_at_ms"] == 7130)
    # Sparse failures (every 10th call) never reach 50 % of the window.
    sparse = simulate(2000, outage=(0, 0), fail_call=lambda i: i % 10 == 9)
    check(sparse["log"] == [] and sparse["rejected"] == 0 and sparse["timed_out"] == 200)
    # Every 2nd call failing reaches 50 % once 10 calls have completed.
    half = simulate(2000, outage=(0, 0), fail_call=lambda i: i % 2 == 1)
    check(half["first_open_ms"] is not None and half["rejected"] > 0)
    # No outage, no failures: breaker stays closed, nothing rejected.
    clean = simulate(2000, outage=(0, 0))
    check((clean["log"], clean["rejected"], clean["succeeded"]) == ([], 0, REQUESTS))
    for name, ms in POLICIES:
        for end in (2000, 3330, 6000, 9010):
            r = simulate(ms, outage=(OUTAGE_START_MS, end))
            check(r["calls"] + r["rejected"] == REQUESTS)
            check(r["succeeded"] + r["timed_out"] == r["calls"])
            check(r["timed_out"] == r["calls_in_outage"])
            check(ms is None or r["closed_at_ms"] is not None)
            # A breaker never lets more calls into the outage than no breaker does.
            check(r["calls_in_outage"] <= (end - OUTAGE_START_MS) // INTERVAL_MS)
        check(simulate(ms) == simulate(ms))
    for kwargs in ({"open_ms": 0}, {"open_ms": 1.5}, {"outage": (5, 1)}, {"outage": (-1, 5)},
                   {"requests": 0}):
        try:
            simulate(**kwargs)
        except ValueError:
            check(True)
        else:
            check(False)
    print(f"regression_checks={checks} PASS")


def summarize(values):
    return median_low(values), min(values), max(values)


def experiment():
    print(f"requests={REQUESTS} every {INTERVAL_MS}ms outage={OUTAGE_START_MS}..{OUTAGE_END_MS}ms "
          f"ok_latency={OK_LATENCY_MS}ms timeout={TIMEOUT_MS}ms window={WINDOW} min_calls={MIN_CALLS} "
          f"failure_rate>={FAILURE_RATE:.0%} half_open_calls={HALF_OPEN_CALLS}")
    print("[main] policy calls succeeded timed_out timeout_wait_ms rejected rejected_after_recovery "
          "peak_in_flight recovery_delay_ms | transitions")
    main_rows = []
    for name, ms in POLICIES:
        r = simulate(ms)
        main_rows.append((name, r))
        print(name, r["calls"], r["succeeded"], r["timed_out"], r["timeout_wait_ms"], r["rejected"],
              r["rejected_after_recovery"], r["peak_in_flight"], r["recovery_delay_ms"], "|",
              " ".join(f"{t}:{to}" for t, to in r["log"]) or "-")
    print(f"[sweep] outage {OUTAGE_START_MS}..end, end={SWEEP_ENDS.start}..{SWEEP_ENDS.stop - 1} "
          f"step {SWEEP_ENDS.step} ({len(SWEEP_ENDS)} runs), median_low [min..max]")
    print("policy timed_out rejected_after_recovery recovery_delay_ms peak_in_flight")
    sweep_rows = []
    for name, ms in POLICIES:
        runs = [simulate(ms, outage=(OUTAGE_START_MS, end)) for end in SWEEP_ENDS]
        row = [name] + [summarize([r[k] for r in runs])
                        for k in ("timed_out", "rejected_after_recovery", "recovery_delay_ms", "peak_in_flight")]
        sweep_rows.append(row)
        print(name, *(f"{m}[{lo}..{hi}]" for m, lo, hi in row[1:]))
    return main_rows, sweep_rows


def write_svg(sweep_rows):
    width, height = 1000, 470
    left, top, bar_h, gap = 230, 120, 34, 22
    most = max(row[1][0] for row in sweep_rows)
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
           '<title id="title">Circuit breaker over 901 outage lengths: timed-out calls, rejections after recovery, time to close</title>',
           '<desc id="desc">One request every 10 ms, outage from 1000 ms to 2000..11000 ms, timeout 1000 ms. Low medians of 901 runs. Deterministic simulation counts, not measured latency.</desc>',
           f'<rect width="{width}" height="{height}" fill="#f8fafc"/>',
           '<g font-family="sans-serif" fill="#0f172a">',
           '<text x="28" y="42" font-size="26" font-weight="bold">Circuit breaker during an outage / 100 req/s</text>',
           '<text x="28" y="74" font-size="16">Bar: calls that hung until the 1000 ms timeout. Right: rejected after recovery, time until CLOSED (low median of 901 outage lengths)</text>']
    for i, row in enumerate(sweep_rows):
        y = top + i * (bar_h + gap)
        timed_out, rejected, delay = row[1][0], row[2][0], row[3][0]
        w = timed_out / most * 300
        svg.append(f'<text x="28" y="{y + 23}" font-size="17">{escape(row[0])}</text>')
        svg.append(f'<rect x="{left}" y="{y}" width="{w:.1f}" height="{bar_h}" fill="#3b82f6"/>')
        svg.append(f'<text x="{left + w + 8:.1f}" y="{y + 23}" font-size="16">{timed_out}</text>')
        svg.append(f'<text x="640" y="{y + 23}" font-size="16">rejected after {rejected} | closed +{delay} ms</text>')
    svg.extend([f'<text x="28" y="{height - 70}" font-size="15">Breaker: last 20 calls, open at 50% failures (min 10), 3 trial calls in HALF_OPEN. Healthy calls take 20 ms.</text>',
                f'<text x="28" y="{height - 44}" font-size="15">Peak calls in flight was 100 in every run: the first failures are only known after the timeout.</text>',
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
    _, sweep_rows = experiment()
    if args.write_svg:
        write_svg(sweep_rows)


if __name__ == "__main__":
    main()
