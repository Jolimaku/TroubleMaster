#!/usr/bin/env python3
"""Full TROUBLESHOOTER mastery-board share-code decoder.

base32 alphabet "23456789ABCDEFGHIJKMNPQRSTUVWXYZ" (5 bits/sym, MSB-first).
PC code:
  bits[0:49]   header (magic + roster/char/job/level)
  bits[49:56]  first group type   = type#+16
  bits[56:64]  first group count  (X&0x7F; n=v-4 if v>=4 else v+12)
  bits[64:]    SCRAMBLED payload. Period-5 transform on the RAW stream:
               raw 5-bit [a,b,c,d,e] -> stored [a, b^c, ~c, d, e].
               Un-scramble, then the raw stream is:
                 [code]*count  then  [type:7][count:8][code]*count  ... per group.
               code = varint, MSB-first byte b7..b0; b7=continuation (2nd byte = high 7 bits).
  groups ordered by ascending type#; trailing zero padding.
"""
import json, re
from pathlib import Path

ALPH = "23456789ABCDEFGHIJKMNPQRSTUVWXYZ"
VAL = {c: i for i, c in enumerate(ALPH)}

def load_maps():
    text = Path("Unpack/Data/xml/MasteryCode.xml").read_text(encoding="utf-8")
    C = {}
    for cm in re.finditer(r'<class name="(\w+)">(.*?)</class>', text, re.S):
        e = {}
        for pm in re.finditer(r'<property\s+([^/]*?)/>', cm.group(2)):
            a = dict(re.findall(r'(\w+)="([^"]*)"', pm.group(1)))
            e[a["name"]] = a
        C[cm.group(1)] = e
    mtype = {n: int(a["Code"]) for n, a in C["MasteryType"].items()}
    tc2name = {(mtype[a["Type"]], int(a["Code"])): n for n, a in C["Mastery"].items()}
    return tc2name

def to_bits(code):
    return [(VAL[c] >> i) & 1 for c in code for i in range(4, -1, -1)]

def unscramble(stored):
    """Invert the period-5 transform [a,b,c,d,e] -> [a, b^c, ~c, d, e]."""
    raw = list(stored)
    for k in range(0, len(stored), 5):
        if k + 2 < len(stored):
            raw[k + 2] = 1 ^ stored[k + 2]
        if k + 1 < len(stored):
            c = raw[k + 2] if k + 2 < len(stored) else 0
            raw[k + 1] = stored[k + 1] ^ c
        # k+0, k+3, k+4 already copied (identity)
    return raw

def _int(bits, p, n):
    return int("".join(map(str, bits[p:p+n])), 2)

def read_varint(raw, p):
    """raw stream, MSB-first byte; b7=continuation -> 2nd byte high 7 bits."""
    byte = _int(raw, p, 8); p += 8
    v = byte & 0x7F
    if byte & 0x80:
        hi = _int(raw, p, 8); p += 8
        v |= (hi & 0x7F) << 7
    return v, p

ALPH = "23456789ABCDEFGHIJKMNPQRSTUVWXYZ"

def scramble(raw):
    """Forward period-5 transform: raw [a,b,c,d,e] -> stored [a, b^c, ~c, d, e]."""
    s = list(raw)
    for k in range(0, len(raw), 5):
        if k + 2 < len(raw): s[k + 2] = 1 ^ raw[k + 2]
        if k + 1 < len(raw): s[k + 1] = raw[k + 1] ^ (raw[k + 2] if k + 2 < len(raw) else 0)
    return s

def _parse_list(raw):                    # raw = unscrambled stream from bit 49 (raw[45:] of full)
    out, p, first = [], 0, True
    while p + 7 <= len(raw):
        if not first: p += 1             # 1-bit separator between groups
        t = _int(raw, p, 7)
        if not (1 <= t <= 85): break     # padding / end
        p += 7
        if p + 8 > len(raw): break
        n = _int(raw, p, 8); p += 8
        for _ in range(n):
            if p + 8 > len(raw): return out, p
            v, p = read_varint(raw, p)
            out.append((t, v))
        first = False
    return out, p

def decode_full(code):
    """Decode header + masteries. The whole code is one period-5 scrambled stream
    (groups aligned so bit 49 is a boundary); un-scramble from bit 4."""
    raw = unscramble(to_bits(code)[4:])           # raw index i == absolute bit (i+4)
    masteries, _ = _parse_list(raw[45:])
    return {
        "rosterType": _int(raw, 16, 4),           # 1=Pc 2=Beast 3=Machine
        "charId": _int(raw, 20, 8),               # Pc/Beast/Machine code (8-bit; PCs fill low nibble)
        "level": _int(raw, 29, 7),
        "jobId": _int(raw, 37, 7),
        "masteries": masteries,                   # list of (type#, code)
    }

def decode_board(code, tc2name=None, header=49):
    """Compat: just the (type#, code) list."""
    return decode_full(code)["masteries"]

def encode_board(template_code, char_id, level, job_id, masteries, roster=1):
    """Build a share code. `template_code` supplies the constant magic; masteries
    is a list of (type#, code). Inverse of decode_full — exact round-trips."""
    b = to_bits(template_code)
    raw = unscramble(b[4:])[:45]                   # header template (raw frame)
    def put(o, w, val):
        for i in range(w): raw[o + i] = (val >> (w - 1 - i)) & 1
    put(16, 4, roster); put(20, 8, char_id); put(29, 7, level); put(37, 7, job_id)
    # build mastery list (ascending type#, codes ascending, 1-bit separators)
    groups = {}
    for t, c in masteries: groups.setdefault(t, []).append(c)
    lst, first = [], True
    for t in sorted(groups):
        codes = sorted(groups[t])
        if not first: lst.append(0)
        first = False
        for i in range(7): lst.append((t >> (6 - i)) & 1)
        for i in range(8): lst.append((len(codes) >> (7 - i)) & 1)
        for c in codes:
            if c < 128:
                for i in range(8): lst.append((c >> (7 - i)) & 1)
            else:
                lo = (c & 0x7f) | 0x80; hi = c >> 7
                for i in range(8): lst.append((lo >> (7 - i)) & 1)
                for i in range(8): lst.append((hi >> (7 - i)) & 1)
    raw = raw + lst
    while (4 + len(raw)) % 5: raw.append(0)        # pad in raw frame, then scramble
    stored = b[0:4] + scramble(raw)
    return "".join(ALPH[_int(stored, i, 5)] for i in range(0, len(stored), 5))

if __name__ == "__main__":
    tc2name = load_maps()
    codeable = set(tc2name.values())
    exp = json.load(open("data/exports/mastery_export.json", encoding="utf-8"))
    ok = bad = 0
    fails = []
    for c in exp["characters"]:
        if c["rosterType"] != "Pc":
            continue
        for bd in c["boards"]:
            if not bd["masteries"]:
                continue
            got = {n for tc in decode_board(bd["code"], tc2name) if (n := tc2name.get(tc)) is not None}
            want = set(m["id"] for m in bd["masteries"] if m["id"] in codeable)
            if got == want:
                ok += 1
            else:
                bad += 1
                if len(fails) < 6:
                    fails.append((c["roster"], bd["index"], f"missing={sorted(want-got)[:5]}", f"extra={sorted(got-want)[:5]}"))
    print(f"FULL BOARD DECODE: OK={ok} BAD={bad}")
    for f in fails:
        print("  FAIL", f)
