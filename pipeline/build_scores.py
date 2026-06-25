# -*- coding: utf-8 -*-
"""
build_scores.py

코맨틀(Comantle) 점수 사전계산 + 검증 스크립트.

동작:
  1) functions.json 의 모든 함수 'description' 을 임베딩한다.
     (로컬 sentence-transformers, 다국어 모델 / 런타임은 인터넷·API 불필요)
  2) 모든 함수쌍의 코사인 유사도를 계산한다.
  3) 정답 기준으로 0~100 정규화한다.
     - 정답 자신의 유사도(=1.0)를 상한(hi)으로 삼는다. 따라서 100점은 '정답'에만 부여되고,
       정답과 아무리 비슷한 비정답이라도 100 미만이 된다.  (결함 A 방지)
     - 하한(lo)은 그 정답과 가장 먼 함수의 유사도.
  4) scores.json 에 캐시한다. (런타임은 임베딩 호출 없이 룩업만)

실행:
  py -3.13 build_scores.py
"""

import json
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
FUNCTIONS_PATH = os.path.join(HERE, "functions.json")
SCORES_PATH = os.path.join(HERE, "scores.json")
MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

ANCHOR_ANSWER = "cpp.vector.push_back"  # 회귀 방지용 스폿 체크 정답


def load_functions():
    with open(FUNCTIONS_PATH, "r", encoding="utf-8") as f:
        funcs = json.load(f)
    ids = [fn["id"] for fn in funcs]
    if len(set(ids)) != len(ids):
        raise SystemExit("functions.json 에 중복된 id 가 있습니다.")
    return funcs


def embed(descriptions):
    from sentence_transformers import SentenceTransformer

    print(f"[build] 임베딩 모델 로드: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)
    print(f"[build] {len(descriptions)}개 설명문 임베딩 중...")
    vecs = model.encode(
        descriptions,
        convert_to_numpy=True,
        normalize_embeddings=True,  # L2 정규화 -> 내적이 곧 코사인, 자기 자신과는 1.0
        show_progress_bar=False,
    )
    return vecs.astype(np.float32)


def build_scores(funcs, vecs):
    """
    각 정답(answer)을 기준으로 0~100 정규화.
      상한 hi = 정답 자기 자신과의 유사도(=1.0)  -> 100 은 정답 전용
      하한 lo = 그 정답과 가장 먼 함수의 유사도
    반환: { answerId: { guessId: score } }
    """
    n = len(funcs)
    ids = [fn["id"] for fn in funcs]
    sim = vecs @ vecs.T  # (n, n), 대각선 = 1.0

    scores = {}
    for i in range(n):
        row = sim[i]
        hi = float(row[i])                      # = 1.0 (정답 자신)
        lo = float(np.delete(row, i).min())     # 가장 먼 함수
        rng = hi - lo if hi > lo else 1e-9

        per_answer = {}
        for j in range(n):
            if i == j:
                per_answer[ids[j]] = 100.0      # 정답만 100
                continue
            norm = (float(row[j]) - lo) / rng
            norm = max(0.0, min(0.9999, norm))  # 비정답은 100 미만으로 고정
            # 4자리로 저장: '진짜 중복(복붙)'만 동점으로 잡히고, 2자리 반올림 우연 일치는 통과.
            per_answer[ids[j]] = round(norm * 100.0, 4)
        scores[ids[i]] = per_answer

    return scores


# --------------------------------------------------------------------------
# 검증 (§5): 전수 정답 루프 + 복붙(코사인) 검사 + 경사 지표
#   FAIL 조건 : (a) 복붙(코사인 4자리=1.0) 쌍 존재
#               (b) 어떤 정답에서든 정답 유일성 위반(정답만 100, 비정답 99.5 미만)
#   경사 "주의": FAIL 아님 — 동료 함수가 빈약한 기능군을 알려주는 신호일 뿐.
# --------------------------------------------------------------------------

def find_copy_paste(funcs, vecs, thresh=1.0):
    """서로 다른 두 함수의 코사인이 4자리에서 1.0 이면 설명문 복붙(정답 무관 1회 검사)."""
    sim = vecs @ vecs.T
    n = len(funcs)
    dups = []
    for i in range(n):
        for j in range(i + 1, n):
            if round(float(sim[i, j]), 4) >= thresh:
                dups.append((funcs[i]["displayName"], funcs[j]["displayName"], float(sim[i, j])))
    return dups


def answer_uniqueness_issues(scores, answer_id, by_id):
    """정답 유일성: 정답만 100, 비정답은 100 미만(99.5 이상이면 사실상 동일 -> FAIL)."""
    row = scores[answer_id]
    issues = []
    if row[answer_id] != 100.0:
        issues.append(f"정답 자신이 100 아님({row[answer_id]})")
    for gid, s in row.items():
        if gid == answer_id:
            continue
        if s >= 100.0:
            issues.append(f"비정답 100 이상: {by_id[gid]['displayName']}={s}")
        elif s >= 99.5:
            issues.append(f"비정답 99.5 이상: {by_id[gid]['displayName']}={s}")
    return issues


def gradient_metrics(scores, answer_id):
    """상위 10개의 폭(1위-10위)과 인접 간격으로 경사가 죽었는지 본다. (warns, spread, top1)"""
    row = scores[answer_id]
    ranked = sorted((g for g in row if g != answer_id), key=lambda g: row[g], reverse=True)
    vals = [row[g] for g in ranked[:10]]
    spread = (vals[0] - vals[-1]) if len(vals) >= 2 else 0.0
    gaps = [vals[k] - vals[k + 1] for k in range(len(vals) - 1)]
    run = best = 0
    for g in gaps:
        run = run + 1 if g < 0.5 else 0
        best = max(best, run)
    warns = []
    if spread < 12.0:
        warns.append(f"상위10 폭 좁음({spread:.1f})")
    # 계단은 '상위권이 함께 압축돼 있을 때'만 의미가 있다. 폭이 넓으면 국소 평탄은 정상(동료 풍부).
    if best >= 3 and spread < 18.0:
        warns.append(f"상위권 계단(간격<0.5 {best}연속)")
    return warns, spread, (vals[0] if vals else 0.0)


def verify(funcs, vecs, scores):
    by_id = {fn["id"]: fn for fn in funcs}
    print("\n" + "=" * 64)
    print(f"[verify] 전수 검증 — 정답 {len(funcs)}개")
    print("=" * 64)
    ok = True

    # (a) 복붙(코사인 1.0) 검사 — 정답 무관 1회
    dups = find_copy_paste(funcs, vecs)
    if dups:
        ok = False
        print(f"[FAIL] 복붙(코사인 4자리=1.0) 쌍 {len(dups)}개:")
        for a, b, c in dups[:30]:
            print(f"   {a} == {b}  (cos={c:.4f})")
    else:
        print("[OK] 복붙 쌍 없음 — 모든 설명문이 서로 다름")

    # (b) 전수 정답 유일성 + 경사 지표
    fails, warns = [], []
    for fn in funcs:
        aid = fn["id"]
        iss = answer_uniqueness_issues(scores, aid, by_id)
        if iss:
            fails.append((aid, iss))
        w, spread, _ = gradient_metrics(scores, aid)
        if w:
            warns.append((aid, w, spread))

    if fails:
        ok = False
        print(f"\n[FAIL] 정답 유일성 위반 {len(fails)}개:")
        for aid, iss in fails[:30]:
            print(f"   {by_id[aid]['displayName']}: {'; '.join(iss)}")
    else:
        print(f"[OK] 전체 {len(funcs)}개 정답에서 정답 유일성 통과 (정답만 100)")

    print(f"\n[요약] 경사 주의 {len(warns)}개 / {len(funcs)}개  (FAIL 아님 — 빈약한 기능군 신호)")
    for aid, w, spread in sorted(warns, key=lambda x: x[2]):
        print(f"   [주의] {by_id[aid]['displayName']:30s} 상위10폭 {spread:5.1f} — {', '.join(w)}")

    # 회귀 방지 스폿 체크: push_back 의 기능적 쌍이 상위권인지
    if ANCHOR_ANSWER in scores:
        row = scores[ANCHOR_ANSWER]
        anchors = ["python.list.append", "java.arraylist.add", "cpp.vector.emplace_back",
                   "cpp.algorithm.sort", "java.io.println"]
        spot = ", ".join(f"{by_id[t]['displayName']}={row[t]:.1f}" for t in anchors if t in row)
        print(f"\n[anchor] push_back → {spot}")

    print("\n" + "=" * 64)
    print(f"[verify] 최종: {'전체 통과 ✅' if ok else '실패 ❌'}  (경사 주의는 통과/실패와 무관)")
    print("=" * 64)
    return ok


def main():
    funcs = load_functions()
    print(f"[build] 함수 {len(funcs)}개 로드")
    descriptions = [fn["description"] for fn in funcs]
    vecs = embed(descriptions)
    scores = build_scores(funcs, vecs)

    out = {
        "meta": {
            "model": MODEL_NAME,
            "count": len(funcs),
            "normalization": "per-answer: (sim - far_min) / (1.0 - far_min), 100 reserved for answer",
        },
        "scores": scores,
    }
    with open(SCORES_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"[build] scores.json 생성 완료 ({len(funcs)}x{len(funcs)})")

    ok = verify(funcs, vecs, scores)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()