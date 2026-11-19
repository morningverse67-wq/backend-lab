"""040. Saga 보상 처리: 중간 단계 실패 시 되돌린 건수와 남은 불일치 (표준 라이브러리만 사용, 시드 고정 시뮬레이션)."""
import argparse
import platform
import random
from pathlib import Path

SEED = 40
ORDERS = 10_000
FAIL_P = {"stock": 0.03, "payment": 0.05, "shipping": 0.04}  # 단계별 실패 확률
COMP_OK, COMP_LOST_REQ, COMP_LOST_RESP = 0.90, 0.05, 0.05     # 보상 호출: 성공 / 요청 유실 / 처리됐지만 응답 유실
MAX_TRIES = 5
SCENARIOS = {
    "none": "보상 없음",
    "once": "보상 1번만 시도",
    "retry": "재시도 (멱등 아님)",
    "idem": "재시도 + 멱등 키",
}
STEPS = ["stock", "payment", "shipping"]
UNDO = {"stock": "재고 해제", "payment": "환불"}


def run(kind):
    rnd = random.Random(SEED)
    r = dict(done=0, failed=0, stuck_stock=0, stuck_pay=0, double_release=0, double_refund=0, comp_calls=0)
    for _ in range(ORDERS):
        ok = []
        failed = False
        for s in STEPS:
            if rnd.random() < FAIL_P[s]:
                failed = True
                break
            ok.append(s)
        if not failed:
            r["done"] += 1
            continue
        r["failed"] += 1
        if kind == "none":
            r["stuck_stock"] += "stock" in ok
            r["stuck_pay"] += "payment" in ok
            continue
        for s in reversed(ok):  # 거꾸로 되돌린다
            applied = 0
            tries = 0
            while True:
                tries += 1
                r["comp_calls"] += 1
                x = rnd.random()
                if x < COMP_OK:
                    outcome = "ok"
                    applied += 1
                elif x < COMP_OK + COMP_LOST_RESP:
                    outcome = "lost_resp"
                    applied += 1
                else:
                    outcome = "lost_req"
                if kind == "idem" and applied > 1:
                    applied = 1  # 같은 키의 보상은 한 번만 반영
                if outcome == "ok" or kind == "once" or tries >= MAX_TRIES:
                    break
            if applied == 0:
                r["stuck_stock" if s == "stock" else "stuck_pay"] += 1
            elif applied > 1:
                r["double_release" if s == "stock" else "double_refund"] += applied - 1
    r["leftover"] = r["stuck_stock"] + r["stuck_pay"] + r["double_release"] + r["double_refund"]
    return r


def regression_tests(res):
    assert res["none"]["done"] + res["none"]["failed"] == ORDERS
    assert res["none"]["leftover"] > res["once"]["leftover"] > 0
    assert res["retry"]["stuck_stock"] + res["retry"]["stuck_pay"] < res["once"]["stuck_stock"] + res["once"]["stuck_pay"]
    assert res["retry"]["double_release"] + res["retry"]["double_refund"] > 0
    assert res["idem"]["double_release"] == 0 and res["idem"]["double_refund"] == 0
    assert res["idem"]["leftover"] < res["retry"]["leftover"]


def experiment():
    res = {k: run(k) for k in SCENARIOS}
    regression_tests(res)
    for k, name in SCENARIOS.items():
        v = res[k]
        print(f"| {name} | {v['failed']:,} | {v['stuck_stock']:,} | {v['stuck_pay']:,} | {v['double_release']:,} | {v['double_refund']:,} | {v['leftover']:,} | {v['comp_calls']:,} |")
    return res


def write_svg(res):
    w, h = 900, 380
    x0, y0, pw, ph = 90, 70, 760, 220
    labels = {"none": "no compensation", "once": "compensate once", "retry": "retry (not idempotent)", "idem": "retry + idempotency key"}
    colors = {"none": "#94a3b8", "once": "#f59e0b", "retry": "#ef4444", "idem": "#2563eb"}
    top = max(v["leftover"] for v in res.values())
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">',
         '<title id="t">Inconsistent records left after failed saga orders</title>',
         '<desc id="d">' + "; ".join(f'{labels[k]} {v["leftover"]:,}' for k, v in res.items()) + '.</desc>',
         f'<rect width="{w}" height="{h}" fill="#f8fafc"/><g font-family="sans-serif" fill="#0f172a">',
         f'<text x="28" y="40" font-size="22" font-weight="bold">Leftover inconsistencies, {ORDERS:,} orders ({res["none"]["failed"]:,} failed)</text>',
         f'<line x1="{x0}" y1="{y0 + ph}" x2="{x0 + pw}" y2="{y0 + ph}" stroke="#94a3b8"/>']
    gw = pw / 4
    bw = 100
    for i, (k, v) in enumerate(res.items()):
        x = x0 + i * gw + (gw - bw) / 2
        bh = v["leftover"] / top * ph
        s.append(f'<rect x="{x:.1f}" y="{y0 + ph - bh:.1f}" width="{bw}" height="{max(bh, 1):.1f}" fill="{colors[k]}"/>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph - bh - 6:.1f}" font-size="13" text-anchor="middle">{v["leftover"]:,}</text>')
        s.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + ph + 22}" font-size="12" text-anchor="middle">{labels[k]}</text>')
    s.append(f'<text x="28" y="{h - 40}" font-size="13">Leftover = unreleased stock + unrefunded payments + double releases + double refunds.</text>')
    s.append(f'<text x="28" y="{h - 14}" font-size="13">Python {platform.python_version()}, seeded simulation (seed {SEED}).</text></g></svg>')
    Path(__file__).with_name("result.svg").write_text("\n".join(s) + "\n", encoding="utf-8")
    print("wrote result.svg")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write-svg", action="store_true")
    a = ap.parse_args()
    res = experiment()
    if a.write_svg:
        write_svg(res)
