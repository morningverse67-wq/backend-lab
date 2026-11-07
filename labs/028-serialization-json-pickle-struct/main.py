"""
Lab 028: Serialization cost - json vs pickle vs struct

The same 100,000 order records are turned into bytes and back with
json, pickle and a fixed-width struct layout. Measures encoded size,
size after zlib, encode time and decode time. Every decoder must give
back exactly the original list of dicts.

Then two cases where the smallest format stops paying off:
  - one record at a time (like a cache value per key)
  - a field is added to the record (schema change)

Run:
    python3 main.py
"""
import gc
import json
import pickle
import platform
import random
import struct
import time
import zlib

N = 100_000
REPEAT = 5
random.seed(28)

STATUSES = ["PAID", "CANCELLED", "REFUNDED", "PENDING"]
STATUS_CODE = {s: i for i, s in enumerate(STATUSES)}
FIELDS = ("order_id", "user_id", "status", "amount", "created_at")

# order_id u32, user_id u32, status u8, amount u32, created_at i64 = 21 bytes
ROW = struct.Struct("<IIBIq")


def make_orders():
    base = 1_767_225_600  # 2026-01-01 00:00:00 UTC
    return [
        {
            "order_id": i,
            "user_id": random.randint(1, 50_000),
            "status": random.choices(STATUSES, weights=[85, 8, 4, 3])[0],
            "amount": random.randint(1, 500) * 100,
            "created_at": base + random.randint(0, 31_536_000),
        }
        for i in range(1, N + 1)
    ]


# ---- batch codecs: list[dict] -> bytes -> list[dict] -------------------

def json_dict_enc(rows):
    return json.dumps(rows, separators=(",", ":")).encode()


def json_dict_dec(data):
    return json.loads(data)


def json_list_enc(rows):
    # same values, no key names: [[order_id, user_id, status, amount, created_at], ...]
    return json.dumps([[r[f] for f in FIELDS] for r in rows], separators=(",", ":")).encode()


def json_list_dec(data):
    return [dict(zip(FIELDS, v)) for v in json.loads(data)]


def pickle_enc(rows):
    return pickle.dumps(rows, protocol=5)


def pickle_dec(data):
    return pickle.loads(data)


def struct_enc(rows):
    buf = bytearray(ROW.size * len(rows))
    pack_into = ROW.pack_into
    off = 0
    for r in rows:
        pack_into(buf, off, r["order_id"], r["user_id"], STATUS_CODE[r["status"]],
                  r["amount"], r["created_at"])
        off += ROW.size
    return bytes(buf)


def struct_dec(data):
    return [
        {"order_id": o, "user_id": u, "status": STATUSES[s], "amount": a, "created_at": c}
        for o, u, s, a, c in ROW.iter_unpack(data)
    ]


CODECS = [
    ("json (dict, key 포함)", json_dict_enc, json_dict_dec),
    ("json (값 배열, key 없음)", json_list_enc, json_list_dec),
    ("pickle (protocol 5)", pickle_enc, pickle_dec),
    ("struct (고정 21바이트)", struct_enc, struct_dec),
]


def best_ms(fn, arg):
    best = float("inf")
    out = None
    for _ in range(REPEAT):
        gc.collect()
        t = time.perf_counter()
        out = fn(arg)
        best = min(best, time.perf_counter() - t)
    return out, best * 1000


# ---- one record at a time ----------------------------------------------

def single_json(r):
    return json.loads(json.dumps(r, separators=(",", ":")).encode())


def single_pickle(r):
    return pickle.loads(pickle.dumps(r, protocol=5))


def single_struct(r):
    o, u, s, a, c = ROW.unpack(ROW.pack(r["order_id"], r["user_id"], STATUS_CODE[r["status"]],
                                        r["amount"], r["created_at"]))
    return {"order_id": o, "user_id": u, "status": STATUSES[s], "amount": a, "created_at": c}


SINGLE_SIZE = {
    "json": lambda r: len(json.dumps(r, separators=(",", ":")).encode()),
    "pickle": lambda r: len(pickle.dumps(r, protocol=5)),
    "struct": lambda r: ROW.size,
}


def main():
    orders = make_orders()
    print(f"Python {platform.python_version()}, records {N:,}, best of {REPEAT}\n")

    print("1) 10만 건을 한 번에 직렬화")
    print(f"{'method':<26}{'bytes':>12}{'zlib':>12}{'encode ms':>11}{'decode ms':>11}")
    batch = {}
    for name, enc, dec in CODECS:
        data, enc_ms = best_ms(enc, orders)
        back, dec_ms = best_ms(dec, data)
        assert back == orders, name
        z = len(zlib.compress(data, 6))
        batch[name] = (len(data), z, enc_ms, dec_ms)
        print(f"{name:<26}{len(data):>12,}{z:>12,}{enc_ms:>11.1f}{dec_ms:>11.1f}")
    js = batch[CODECS[0][0]]
    st = batch[CODECS[3][0]]
    print(f"   size json/struct {js[0] / st[0]:.2f}x, after zlib {js[1] / st[1]:.2f}x")
    print(f"   enc+dec json {js[2] + js[3]:.1f}ms vs struct {st[2] + st[3]:.1f}ms\n")

    print("2) 한 건씩 직렬화 (캐시 값 하나처럼), 10만 번 encode+decode")
    sample = orders
    print(f"{'method':<10}{'bytes/1건':>10}{'total ms':>10}{'us/건':>8}")
    for name, fn in (("json", single_json), ("pickle", single_pickle), ("struct", single_struct)):
        best = float("inf")
        for _ in range(REPEAT):
            gc.collect()
            t = time.perf_counter()
            out = [fn(r) for r in sample]
            best = min(best, time.perf_counter() - t)
        assert out == sample, name
        ms = best * 1000
        print(f"{name:<10}{SINGLE_SIZE[name](orders[0]):>10}{ms:>10.1f}{ms * 1000 / N:>8.2f}")

    print("\n3) 필드 하나(coupon_id)를 추가한 새 데이터를 옛 코드가 읽으면")
    new_row = dict(orders[0], coupon_id=7)
    new_json = json.dumps(new_row).encode()
    old_json = json.loads(new_json)
    print(f"   json  : {'OK' if all(old_json[f] == orders[0][f] for f in FIELDS) else 'MISMATCH'} (모르는 key는 무시)")
    NEW_ROW = struct.Struct("<IIBIqI")
    new_struct = NEW_ROW.pack(*(orders[0][f] if f != "status" else STATUS_CODE[orders[0][f]] for f in FIELDS), 7)
    try:
        ROW.unpack(new_struct)
        print("   struct: OK")
    except struct.error as e:
        print(f"   struct: struct.error ({e})")
    # 21 new rows x 25 bytes = 525 bytes = 25 old rows x 21 bytes: no error, wrong data
    new_rows = [dict(r, coupon_id=7) for r in orders[:21]]
    blob = b"".join(
        NEW_ROW.pack(r["order_id"], r["user_id"], STATUS_CODE[r["status"]], r["amount"],
                     r["created_at"], r["coupon_id"])
        for r in new_rows
    )
    misread = list(ROW.iter_unpack(blob))
    correct = sum(1 for i, v in enumerate(misread[:21])
                  if v[0] == orders[i]["order_id"] and v[3] == orders[i]["amount"])
    assert len(misread) == 25
    print(f"   struct, 새 형식 21건을 옛 코드로 읽기: 에러 없이 {len(misread)}건으로 읽힘, "
          f"order_id·amount가 맞는 행 {correct}건")

if __name__ == "__main__":
    main()
