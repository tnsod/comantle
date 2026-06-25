# -*- coding: utf-8 -*-
"""
sync_data.py — 빌드 산출물(점수표)을 백엔드 데이터로 동기화.

build_scores.py 가 루트에 functions.json / scores.json 을 갱신하면, 서버가 읽는
backend/data/ 로 복사한다. (data.js 는 A안에서 더 이상 쓰지 않으므로 복사하지 않는다.)

  py -3.13 build_scores.py   # 점수표 갱신 (scores.json / data.js 생성)
  py -3.13 sync_data.py      # backend/data 로 복사

scores.json 은 '서버 전용'이며 프론트로는 절대 배포하지 않는다(부정 방지 §0).
"""
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
DST = os.path.join(HERE, "backend", "data")

FILES = ["functions.json", "scores.json"]


def main():
    os.makedirs(DST, exist_ok=True)
    for name in FILES:
        src = os.path.join(HERE, name)
        if not os.path.exists(src):
            raise SystemExit(f"[sync] 원본 없음: {src} — 먼저 build_scores.py 를 실행하세요.")
        dst = os.path.join(DST, name)
        shutil.copy2(src, dst)
        print(f"[sync] {name} -> backend/data/{name} ({os.path.getsize(dst):,} bytes)")
    print("[sync] done. scores.json is server-only - do NOT ship to frontend.")


if __name__ == "__main__":
    main()
