# -*- coding: utf-8 -*-
"""앵커 정답의 상위권 경사 수치 기록 (배치 간 회귀 비교용). ASCII 출력만."""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
scores = json.load(open(os.path.join(HERE, "scores.json"), encoding="utf-8"))["scores"]
funcs = json.load(open(os.path.join(HERE, "functions.json"), encoding="utf-8"))
byid = {f["id"]: f for f in funcs}

anchors = sys.argv[1:] or [
    "cpp.vector.push_back", "cpp.algorithm.sort", "python.builtin.len",
]
print(f"# pool size = {len(funcs)}")
for a in anchors:
    if a not in scores:
        print(a, "MISSING"); continue
    row = scores[a]
    ranked = sorted(((g, s) for g, s in row.items() if g != a), key=lambda x: -x[1])
    top = ranked[:10]
    t1 = top[0][1]
    t10 = top[-1][1]
    spread = t1 - t10
    name = byid[a]["displayName"]
    # ASCII-safe label (avoid non-cp949 chars)
    print(f"[{a}] top1={t1:.1f} top10={t10:.1f} spread={spread:.1f}")
    for g, s in ranked[:6]:
        print(f"    {s:5.1f}  {byid[g]['displayName']} ({byid[g]['language']})")
