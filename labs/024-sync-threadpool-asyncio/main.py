"""Sequential vs thread pool vs asyncio for 100 calls to a slow local HTTP server.

Real wall-clock measurement on localhost; Python standard library only.
The server runs in a separate process and only waits (asyncio.sleep) before answering.
"""
from concurrent.futures import ThreadPoolExecutor
from math import ceil
from pathlib import Path
from statistics import median_low
import argparse
import asyncio
import platform
import socket
import subprocess
import sys
import time
from xml.sax.saxutils import escape

N = 100                 # calls per run
DELAY_MS = 50           # the server waits 50 ms before each answer (stands in for an external API)
CPU_LOOP = 200_000      # scenario B: pure-Python loop after each answer (client CPU time is measured)
REPEATS = 5             # runs per mode; tables show median_low [min..max]
POOL_WORKERS = (4, 16, 100)
ASYNC_LIMIT = 16        # asyncio with a semaphore, to compare with a 16-thread pool


# ---------------------------------------------------------------- server (separate process)
def serve():
    """GET /<i> -> wait DELAY_MS -> body '<i>'. GET /stats -> peak in-flight since last /reset."""
    state = {"inflight": 0, "peak": 0}

    async def handle(reader, writer):
        line = await reader.readline()
        while (await reader.readline()) not in (b"\r\n", b""):
            pass
        path = line.split()[1].decode()
        if path == "/reset":
            state["peak"] = 0
            body = "ok"
        elif path == "/stats":
            body = str(state["peak"])
        else:
            state["inflight"] += 1
            state["peak"] = max(state["peak"], state["inflight"])
            await asyncio.sleep(DELAY_MS / 1000)
            state["inflight"] -= 1
            body = path[1:]
        data = body.encode()
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s" % (len(data), data))
        await writer.drain()
        writer.close()

    async def main():
        server = await asyncio.start_server(handle, "127.0.0.1", 0, backlog=1024)
        print(server.sockets[0].getsockname()[1], flush=True)
        async with server:
            await server.serve_forever()

    asyncio.run(main())


# ---------------------------------------------------------------- clients (same bytes on the wire)
def request_bytes(path):
    return f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n".encode()


def body_of(raw):
    head, _, body = raw.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 200 OK"), head
    return body.decode()


def get(port, path):
    """Blocking call: the calling thread waits until the answer arrives."""
    with socket.create_connection(("127.0.0.1", port)) as s:
        s.sendall(request_bytes(path))
        chunks = []
        while chunk := s.recv(4096):
            chunks.append(chunk)
    return body_of(b"".join(chunks))


async def aget(port, path):
    """Non-blocking call: the event loop runs other coroutines while this one waits."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request_bytes(path))
    await writer.drain()
    raw = await reader.read()
    writer.close()
    await writer.wait_closed()
    return body_of(raw)


def cpu_work(loop=CPU_LOOP):
    total = 0
    for i in range(loop):
        total += i * i % 7
    return total


def call(port, i, cpu):
    body = get(port, f"/{i}")
    if cpu:
        cpu_work()
    return body


async def acall(port, i, cpu):
    body = await aget(port, f"/{i}")
    if cpu:
        cpu_work()   # runs on the event loop thread: nothing else runs meanwhile
    return body


def run_sequential(port, n, cpu=False):
    return [call(port, i, cpu) for i in range(n)]


def run_pool(port, n, workers, cpu=False):
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(lambda i: call(port, i, cpu), range(n)))


def run_async(port, n, limit=None, cpu=False):
    if limit is not None and limit < 1:
        raise ValueError("limit must be at least 1")   # Semaphore(0) would wait forever

    async def main():
        sem = asyncio.Semaphore(limit or n)

        async def one(i):
            async with sem:
                return await acall(port, i, cpu)
        return await asyncio.gather(*(one(i) for i in range(n)))
    return asyncio.run(main())


def run_async_blocking(port, n, cpu=False):
    """Common mistake: a blocking client inside async def. gather() cannot overlap it."""
    async def main():
        async def one(i):
            return call(port, i, cpu)
        return await asyncio.gather(*(one(i) for i in range(n)))
    return asyncio.run(main())


MODES = [  # name, function, concurrency limit used for the lower bound
    ("sequential", lambda p, n, cpu: run_sequential(p, n, cpu), 1),
    ("thread pool 4", lambda p, n, cpu: run_pool(p, n, 4, cpu), 4),
    ("thread pool 16", lambda p, n, cpu: run_pool(p, n, 16, cpu), 16),
    ("thread pool 100", lambda p, n, cpu: run_pool(p, n, 100, cpu), 100),
    (f"asyncio limit {ASYNC_LIMIT}", lambda p, n, cpu: run_async(p, n, ASYNC_LIMIT, cpu), ASYNC_LIMIT),
    ("asyncio gather", lambda p, n, cpu: run_async(p, n, None, cpu), N),
    ("asyncio + blocking call", lambda p, n, cpu: run_async_blocking(p, n, cpu), 1),
]


def measure(port, fn, n, cpu):
    get(port, "/reset")
    c0, t0 = time.process_time(), time.perf_counter()
    bodies = fn(port, n, cpu)
    wall = (time.perf_counter() - t0) * 1000
    cpu_ms = (time.process_time() - c0) * 1000   # CPU used by all client threads together
    peak = int(get(port, "/stats"))
    assert bodies == [str(i) for i in range(n)], "every call must get its own answer, in order"
    return wall, peak, cpu_ms


# ---------------------------------------------------------------- checks
def regression_tests(port):
    checks = 0

    def check(condition):
        nonlocal checks
        assert condition
        checks += 1

    # one call takes at least the server delay, and the server sees exactly one in flight
    for fn in (lambda: get(port, "/7"), lambda: asyncio.run(aget(port, "/7"))):
        get(port, "/reset")
        t0 = time.perf_counter()
        check(fn() == "7")
        check((time.perf_counter() - t0) * 1000 >= DELAY_MS)
        check(get(port, "/stats") == "1")
    # small hand-checked cases: 8 calls, 50 ms each
    for name, fn, limit in MODES:
        lim = min(limit, 8)
        wall, peak, _ = measure(port, fn, 8, False)
        check(wall >= ceil(8 / lim) * DELAY_MS)        # cannot beat ceil(n / concurrency) rounds
        check(1 <= peak <= lim)                       # never more in flight than the limit allows
        if lim == 1:
            check(peak == 1)
    # answers are matched to their own request even when they finish out of order
    check(run_pool(port, 30, 30) == [str(i) for i in range(30)])
    check(run_async(port, 30) == [str(i) for i in range(30)])
    # input validation
    for bad in (lambda: run_pool(port, 1, 0), lambda: run_async(port, 1, limit=0)):
        try:
            bad()
        except ValueError:
            check(True)
        else:
            check(False)
    print(f"regression_checks={checks} PASS")
    return checks


# ---------------------------------------------------------------- experiment
def experiment(port):
    print(f"calls={N}, server delay={DELAY_MS}ms, repeats={REPEATS}, scenario B cpu loop={CPU_LOOP:,} iterations per call, "
          f"gil_enabled={getattr(sys, '_is_gil_enabled', lambda: True)()}")
    print("values: median_low over repeats [min..max]; wall = real elapsed ms for all calls; "
          "peak = max in flight seen by server; client_cpu = process CPU ms of the client")
    table = {}
    for scenario, cpu in (("A io only", False), ("B io + cpu", True)):
        print(f"[{scenario}] mode | wall_ms | peak_in_flight | client_cpu_ms | lower_bound_ms")
        for name, fn, limit in MODES:
            runs = [measure(port, fn, N, cpu) for _ in range(REPEATS)]
            walls, peaks, cpus = [r[0] for r in runs], [r[1] for r in runs], [r[2] for r in runs]
            bound = ceil(N / limit) * DELAY_MS
            for w in walls:
                assert w >= bound
            assert max(peaks) <= limit
            table[scenario, name] = dict(wall=(median_low(walls), min(walls), max(walls)),
                                         peak=(median_low(peaks), min(peaks), max(peaks)),
                                         cpu=(median_low(cpus), min(cpus), max(cpus)), bound=bound)
            print(f"{name} | {median_low(walls):.1f} [{min(walls):.1f}..{max(walls):.1f}] | "
                  f"{median_low(peaks)} [{min(peaks)}..{max(peaks)}] | "
                  f"{median_low(cpus):.1f} [{min(cpus):.1f}..{max(cpus):.1f}] | {bound}")
    return table


def write_svg(table):
    names = [m[0] for m in MODES]
    width, height = 1000, 600
    left, right, top = 250, 900, 150
    row_h, bar_h = 54, 18
    colors = {"A io only": "#2563eb", "B io + cpu": "#dc2626"}
    labels = {"A io only": "A: I/O only (server waits 50 ms)", "B io + cpu": f"B: I/O + Python loop of {CPU_LOOP:,} steps per call"}
    hi_ms = 10_000
    from math import log10

    def x(ms):
        return left + log10(max(ms, 10) / 10) / log10(hi_ms / 10) * (right - left)

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
           '<title id="title">Time for 100 slow calls: sequential vs thread pool vs asyncio</title>',
           f'<desc id="desc">100 calls to a local server that waits 50 ms. Wall time, low median of {REPEATS} runs, log scale.</desc>',
           f'<rect width="{width}" height="{height}" fill="#f8fafc"/>',
           '<g font-family="sans-serif" fill="#0f172a">',
           '<text x="28" y="42" font-size="26" font-weight="bold">100 calls to a 50 ms server: total wall time</text>',
           f'<text x="28" y="74" font-size="16">Real elapsed ms on one machine (log scale), low median of {REPEATS} runs.</text>']
    for k, name in enumerate(colors):
        lx = 28 + k * 380
        svg.append(f'<rect x="{lx}" y="98" width="16" height="16" fill="{colors[name]}"/>')
        svg.append(f'<text x="{lx + 22}" y="112" font-size="14">{escape(labels[name])}</text>')
    bottom = top + row_h * len(names)
    for ms in (10, 100, 1000, 10000):
        svg.append(f'<line x1="{x(ms):.1f}" y1="{top - 6}" x2="{x(ms):.1f}" y2="{bottom}" stroke="#cbd5e1"/>')
        svg.append(f'<text x="{x(ms):.1f}" y="{bottom + 22}" font-size="14" text-anchor="middle">{ms:,} ms</text>')
    for r, name in enumerate(names):
        y0 = top + r * row_h
        svg.append(f'<text x="{left - 12}" y="{y0 + bar_h + 4}" font-size="15" text-anchor="end">{escape(name)}</text>')
        for k, scenario in enumerate(colors):
            ms = table[scenario, name]["wall"][0]
            y = y0 + k * (bar_h + 2)
            svg.append(f'<rect x="{left}" y="{y}" width="{x(ms) - left:.1f}" height="{bar_h}" fill="{colors[scenario]}"/>')
            svg.append(f'<text x="{x(ms) + 6:.1f}" y="{y + 14}" font-size="13" fill="{colors[scenario]}">{ms:,.0f}</text>')
    svg.extend([f'<text x="28" y="{height - 22}" font-size="14">The server runs in its own process. Python {platform.python_version()} with the GIL; numbers vary by machine.</text>',
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
