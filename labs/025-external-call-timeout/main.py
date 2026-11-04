"""An external call without a timeout blocks a small server, including requests that never call it.

Real wall-clock measurement on localhost; Python standard library only.
The dependency (a stand-in for an external API) runs in a separate process.
The app has 8 worker threads that accept connections themselves, like a fixed-size request thread pool.
"""
from pathlib import Path
from statistics import median_low
from threading import Lock, Thread
import argparse
import asyncio
import platform
import socket
import subprocess
import sys
import time
from xml.sax.saxutils import escape

WORKERS = 8             # app worker threads (request thread pool)
NORMAL_MS = 20          # healthy dependency answers after 20 ms
STALL_MS = 2_000        # stalled dependency accepts the connection, then says nothing for 2 s
DRIP_BYTES = 20         # dripping dependency sends headers, then 1 body byte every 100 ms
DRIP_GAP_MS = 100
TIMEOUT_MS = 200        # timeout used by the app for its dependency call
ORDERS, ORDER_EVERY_MS = 40, 50                         # /order: 20 per second for 2 s
HEALTHS, HEALTH_EVERY_MS, HEALTH_OFFSET_MS = 20, 100, 25  # /health: 10 per second, never calls the dependency
REPEATS = 3
CLIENT_CAP_S = 60       # the load generator itself never waits forever
POLICIES = ("none", "connect", "read", "deadline")

ROWS = [  # name, dependency mode, app call policy, expected /order status
    ("normal, no timeout", "normal", "none", 200),
    ("stall, no timeout", "stall", "none", 200),
    ("stall, connect timeout only", "stall", "connect", 200),
    ("stall, read timeout 200 ms", "stall", "read", 504),
    ("drip, read timeout 200 ms", "drip", "read", 200),
    ("drip, total deadline 200 ms", "drip", "deadline", 504),
]


# ---------------------------------------------------------------- dependency (separate process)
def serve():
    """GET /<mode>/<i> -> body str(i).zfill(DRIP_BYTES). GET /stats -> "peak,inflight" (peak since /reset)."""
    state = {"inflight": 0, "peak": 0}

    async def handle(reader, writer):
        try:
            line = await reader.readline()
            while (await reader.readline()) not in (b"\r\n", b""):
                pass
            path = line.split()[1].decode()
            if path in ("/reset", "/stats"):
                if path == "/reset":
                    state["peak"] = 0
                body = f'{state["peak"]},{state["inflight"]}'.encode()
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s" % (len(body), body))
                await writer.drain()
                return
            _, mode, i = path.split("/")
            body = i.zfill(DRIP_BYTES).encode()
            head = b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % len(body)
            state["inflight"] += 1
            state["peak"] = max(state["peak"], state["inflight"])
            try:
                if mode == "normal":
                    await asyncio.sleep(NORMAL_MS / 1000)
                    writer.write(head + body)
                elif mode == "stall":
                    await asyncio.sleep(STALL_MS / 1000)
                    writer.write(head + body)
                elif mode == "drip":
                    writer.write(head)
                    for b in body:
                        await writer.drain()
                        await asyncio.sleep(DRIP_GAP_MS / 1000)
                        writer.write(bytes([b]))
                await writer.drain()
            finally:
                state["inflight"] -= 1   # work continues here even if the caller already gave up
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()

    async def main():
        server = await asyncio.start_server(handle, "127.0.0.1", 0, backlog=1024)
        print(server.sockets[0].getsockname()[1], flush=True)
        async with server:
            await server.serve_forever()

    asyncio.run(main())


# ---------------------------------------------------------------- HTTP helpers
def request_bytes(path):
    return f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n".encode()


def response_bytes(status, body):
    reason = {200: "OK", 504: "Gateway Timeout"}[status]
    data = body.encode()
    return b"HTTP/1.1 %d %s\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s" % (status, reason.encode(), len(data), data)


def parse(raw):
    head, _, body = raw.partition(b"\r\n\r\n")
    status = int(head.split(b" ", 2)[1])
    return status, body.decode()


def fetch(port, path, policy, timeout_ms=TIMEOUT_MS):
    """The app's call to the dependency.

    none     : no timeout at all
    connect  : timeout only while connecting, then wait forever for the answer
    read     : timeout on connect and on every recv() (resets each time a byte arrives)
    deadline : one total budget for the whole call
    Raises TimeoutError when a timeout fires.
    """
    if policy not in POLICIES:
        raise ValueError(f"unknown policy {policy!r}")
    if timeout_ms <= 0:
        raise ValueError("timeout_ms must be positive")
    budget = timeout_ms / 1000
    deadline = time.perf_counter() + budget
    with socket.create_connection(("127.0.0.1", port), timeout=None if policy == "none" else budget) as s:
        if policy == "connect":
            s.settimeout(None)
        s.sendall(request_bytes(path))
        chunks = []
        while True:
            if policy == "deadline":
                left = deadline - time.perf_counter()
                if left <= 0:
                    raise TimeoutError("deadline exceeded")
                s.settimeout(left)
            chunk = s.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    status, body = parse(b"".join(chunks))
    assert status == 200
    return body


def http_get(port, path, timeout_s=CLIENT_CAP_S):
    with socket.create_connection(("127.0.0.1", port), timeout=timeout_s) as s:
        s.sendall(request_bytes(path))
        chunks = []
        while chunk := s.recv(4096):
            chunks.append(chunk)
    return parse(b"".join(chunks))


# ---------------------------------------------------------------- app (8 worker threads in this process)
class App:
    def __init__(self, dep_port, mode, policy, workers=WORKERS):
        if workers < 1:
            raise ValueError("workers must be at least 1")
        self.dep_port, self.mode, self.policy = dep_port, mode, policy
        self.sock = socket.create_server(("127.0.0.1", 0), backlog=128)
        self.port = self.sock.getsockname()[1]
        self.lock = Lock()
        self.busy = self.peak_busy = 0
        self.threads = [Thread(target=self.worker, daemon=True) for _ in range(workers)]
        for t in self.threads:
            t.start()

    def worker(self):
        while True:
            conn, _ = self.sock.accept()   # waiting connections stay in the listen backlog
            with conn:
                raw = b""
                while b"\r\n\r\n" not in raw:
                    raw += conn.recv(4096)
                path = raw.split()[1].decode()
                if path == "/quit":
                    conn.sendall(response_bytes(200, "bye"))
                    return
                with self.lock:
                    self.busy += 1
                    self.peak_busy = max(self.peak_busy, self.busy)
                try:
                    if path == "/health":
                        status, body = 200, "ok"
                    else:
                        i = path.removeprefix("/order/")
                        try:
                            status, body = 200, fetch(self.dep_port, f"/{self.mode}/{i}", self.policy)
                        except TimeoutError:
                            status, body = 504, "dependency timeout"
                    conn.sendall(response_bytes(status, body))
                finally:
                    with self.lock:
                        self.busy -= 1

    def close(self):
        for _ in self.threads:
            http_get(self.port, "/quit")
        for t in self.threads:
            t.join()
        self.sock.close()


def run_load(app_port):
    """Open-loop load: requests leave on schedule whether or not earlier ones finished."""
    events = sorted([(k * ORDER_EVERY_MS, "order", k) for k in range(ORDERS)] +
                    [(HEALTH_OFFSET_MS + k * HEALTH_EVERY_MS, "health", k) for k in range(HEALTHS)])
    results, lock, threads = [], Lock(), []
    t0 = time.perf_counter()

    def one(kind, k):
        start = time.perf_counter()
        status, body = http_get(app_port, "/health" if kind == "health" else f"/order/{k}")
        end = time.perf_counter()
        with lock:
            results.append((kind, k, status, body, (end - start) * 1000, (end - t0) * 1000))

    for at, kind, k in events:
        wait = t0 + at / 1000 - time.perf_counter()
        if wait > 0:
            time.sleep(wait)
        th = Thread(target=one, args=(kind, k))
        th.start()
        threads.append(th)
    for th in threads:
        th.join()
    assert len(results) == ORDERS + HEALTHS
    return results


def dependency_stats(dep_port):
    peak, inflight = http_get(dep_port, "/stats")[1].split(",")
    return int(peak), int(inflight)


def wait_dependency_idle(dep_port):
    """Calls the app gave up on may still be running in the dependency; start each run from zero."""
    end = time.perf_counter() + 10
    while dependency_stats(dep_port)[1] > 0:
        assert time.perf_counter() < end, "dependency did not go idle"
        time.sleep(0.05)
    http_get(dep_port, "/reset")


def measure(dep_port, mode, policy, expected):
    wait_dependency_idle(dep_port)
    app = App(dep_port, mode, policy)
    try:
        results = run_load(app.port)
    finally:
        app.close()
    dep_peak = dependency_stats(dep_port)[0]
    health = sorted(r[4] for r in results if r[0] == "health")
    orders = [r for r in results if r[0] == "order"]
    for kind, k, status, body, ms, _ in results:
        if kind == "health":
            assert (status, body) == (200, "ok")
        else:
            assert status == expected, (kind, k, status)
            if status == 200:
                assert body == str(k).zfill(DRIP_BYTES), "every order must get its own answer"
                assert ms >= {"normal": NORMAL_MS, "stall": STALL_MS, "drip": DRIP_BYTES * DRIP_GAP_MS}[mode]
            else:
                assert ms >= TIMEOUT_MS
    order_ms = sorted(r[4] for r in orders)
    return dict(health_p50=median_low(health), health_max=health[-1],
                order_p50=median_low(order_ms), order_max=order_ms[-1],
                ok=sum(r[2] == 200 for r in orders), timeouts=sum(r[2] == 504 for r in orders),
                total=max(r[5] for r in results), app_peak=app.peak_busy, dep_peak=dep_peak)


# ---------------------------------------------------------------- checks
def regression_tests(dep_port):
    checks = 0

    def check(condition):
        nonlocal checks
        assert condition
        checks += 1

    def timed(fn):
        t0 = time.perf_counter()
        try:
            out = fn()
        except TimeoutError:
            out = TimeoutError
        return out, (time.perf_counter() - t0) * 1000

    want = "7".zfill(DRIP_BYTES)
    for policy in POLICIES:                              # a healthy call works with every policy
        out, ms = timed(lambda: fetch(dep_port, "/normal/7", policy))
        check(out == want)
        check(ms >= NORMAL_MS)
    for policy in ("none", "connect"):                   # no read timeout: wait for the whole stall
        out, ms = timed(lambda: fetch(dep_port, "/stall/7", policy))
        check(out == want)
        check(ms >= STALL_MS)
    for policy in ("read", "deadline"):                  # a read timeout or deadline cuts the stall
        out, ms = timed(lambda: fetch(dep_port, "/stall/7", policy))
        check(out is TimeoutError)
        check(TIMEOUT_MS <= ms < TIMEOUT_MS + 300)
    out, ms = timed(lambda: fetch(dep_port, "/drip/7", "read"))   # a byte every 100 ms resets a 200 ms read timeout
    check(out == want)
    check(ms >= DRIP_BYTES * DRIP_GAP_MS)
    out, ms = timed(lambda: fetch(dep_port, "/drip/7", "deadline"))
    check(out is TimeoutError)
    check(TIMEOUT_MS <= ms < TIMEOUT_MS + 300)
    # app: with 8 workers stuck on a stalled call, /health waits too; with a read timeout it does not
    for policy, slow in (("none", True), ("read", False)):
        app = App(dep_port, "stall", policy)
        try:
            idle = http_get(app.port, "/health")
            hold = [Thread(target=http_get, args=(app.port, f"/order/{k}")) for k in range(WORKERS)]
            for th in hold:
                th.start()
            time.sleep(0.05)
            (status, body), ms = timed(lambda: http_get(app.port, "/health"))
            for th in hold:
                th.join()
        finally:
            app.close()
        check(idle == (200, "ok") and (status, body) == (200, "ok"))
        check(ms >= STALL_MS - 200 if slow else ms < TIMEOUT_MS + 200)
    app = App(dep_port, "normal", "none")
    try:
        check(http_get(app.port, "/order/5") == (200, "5".zfill(DRIP_BYTES)))
    finally:
        app.close()
    for bad in (lambda: fetch(dep_port, "/normal/1", "socket"), lambda: fetch(dep_port, "/normal/1", "read", 0),
                lambda: App(dep_port, "normal", "none", workers=0)):
        try:
            bad()
        except ValueError:
            check(True)
        else:
            check(False)
    print(f"regression_checks={checks} PASS")
    return checks


# ---------------------------------------------------------------- experiment
def experiment(dep_port):
    print(f"app workers={WORKERS}, /order every {ORDER_EVERY_MS}ms x{ORDERS}, /health every {HEALTH_EVERY_MS}ms x{HEALTHS}, "
          f"timeout={TIMEOUT_MS}ms, stall={STALL_MS}ms, drip={DRIP_BYTES} bytes x {DRIP_GAP_MS}ms, repeats={REPEATS}")
    print("values: median_low over repeats [min..max] of each run's statistic; latency = request sent -> answer received (ms)")
    keys = ("health_p50", "health_max", "order_p50", "order_max", "ok", "timeouts", "total", "app_peak", "dep_peak")
    print("row | " + " | ".join(keys))
    table = {}
    for name, mode, policy, expected in ROWS:
        runs = [measure(dep_port, mode, policy, expected) for _ in range(REPEATS)]
        table[name] = {k: (median_low([r[k] for r in runs]), min(r[k] for r in runs), max(r[k] for r in runs)) for k in keys}
        cells = []
        for k in keys:
            mid, lo, hi = table[name][k]
            fmt = "{:.1f}" if isinstance(mid, float) else "{}"
            cells.append(f"{fmt.format(mid)} [{fmt.format(lo)}..{fmt.format(hi)}]")
        print(f"{name} | " + " | ".join(cells))
    return table


def write_svg(table):
    from math import log10
    names = [r[0] for r in ROWS]
    width, height = 1000, 560
    left, right, top = 280, 900, 150
    row_h, bar_h = 54, 18
    series = [("health_max", "#2563eb", "/health slowest (never calls the dependency)"),
              ("order_p50", "#dc2626", "/order median")]
    lo_ms, hi_ms = 0.1, 20_000

    def x(ms):
        return left + log10(max(ms, lo_ms) / lo_ms) / log10(hi_ms / lo_ms) * (right - left)

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
           '<title id="title">An external call without a timeout blocks unrelated requests</title>',
           f'<desc id="desc">App with {WORKERS} worker threads. Slowest health check and median order latency, low median of {REPEATS} runs, log scale.</desc>',
           f'<rect width="{width}" height="{height}" fill="#f8fafc"/>',
           '<g font-family="sans-serif" fill="#0f172a">',
           f'<text x="28" y="42" font-size="26" font-weight="bold">{WORKERS} app workers, slow dependency: latency per request</text>',
           f'<text x="28" y="74" font-size="16">Real elapsed ms on one machine (log scale), low median of {REPEATS} runs. Timeout = {TIMEOUT_MS} ms.</text>']
    for k, (_, color, label) in enumerate(series):
        lx = 28 + k * 420
        svg.append(f'<rect x="{lx}" y="98" width="16" height="16" fill="{color}"/>')
        svg.append(f'<text x="{lx + 22}" y="112" font-size="14">{escape(label)}</text>')
    bottom = top + row_h * len(names)
    for ms in (0.1, 1, 10, 100, 1000, 10000):
        svg.append(f'<line x1="{x(ms):.1f}" y1="{top - 6}" x2="{x(ms):.1f}" y2="{bottom}" stroke="#cbd5e1"/>')
        svg.append(f'<text x="{x(ms):.1f}" y="{bottom + 22}" font-size="14" text-anchor="middle">{ms:,g} ms</text>')
    for r, name in enumerate(names):
        y0 = top + r * row_h
        svg.append(f'<text x="{left - 12}" y="{y0 + bar_h + 4}" font-size="15" text-anchor="end">{escape(name)}</text>')
        for k, (key, color, _) in enumerate(series):
            ms = table[name][key][0]
            y = y0 + k * (bar_h + 2)
            svg.append(f'<rect x="{left}" y="{y}" width="{x(ms) - left:.1f}" height="{bar_h}" fill="{color}"/>')
            svg.append(f'<text x="{x(ms) + 6:.1f}" y="{y + 14}" font-size="13" fill="{color}">{ms:.1f}</text>' if ms < 10 else
                       f'<text x="{x(ms) + 6:.1f}" y="{y + 14}" font-size="13" fill="{color}">{ms:,.0f}</text>')
    svg.extend([f'<text x="28" y="{height - 22}" font-size="14">The dependency runs in its own process. Python {platform.python_version()}; numbers vary by machine.</text>',
                '</g></svg>'])
    Path(__file__).with_name("result.svg").write_text("\n".join(svg) + "\n", encoding="utf-8")
    print("wrote result.svg")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-svg", action="store_true", help="regenerate result.svg from checked results")
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve:
        serve()
        raise SystemExit
    if not __debug__:
        raise SystemExit("Run without -O: assertions are required")
    print(f"Python {platform.python_version()} | {platform.system()} {platform.release()} | {platform.machine()}")
    proc = subprocess.Popen([sys.executable, __file__, "--serve"], stdout=subprocess.PIPE, text=True)
    try:
        port = int(proc.stdout.readline())
        regression_tests(port)
        table = experiment(port)
        if args.write_svg:
            write_svg(table)
    finally:
        proc.terminate()
        proc.wait()
