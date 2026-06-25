# -*- coding: utf-8 -*-
"""
game.py — 코맨틀 부정 방지 백엔드의 게임 로직.

핵심 계약(§0):
  - scores.json(점수표)은 메모리에 올려 서버에서만 룩업한다. 클라에 내려보내지 않는다.
  - 임베딩 런타임 호출 금지. 사전계산된 scores.json 을 룩업만 한다.
  - 정답 id 는 '맞히기/포기 전'에는 어떤 응답에도 싣지 않는다 (이 모듈은 데이터만 제공하고,
    노출 여부는 main.py 의 엔드포인트가 통제한다).

정답 결정(§1):
  - DB 없음. 날짜 문자열로 결정론적으로 뽑는다.
  - hashStr 은 프론트(app.js)의 djb2(부호 없는 32비트)와 '동일 알고리즘'을 파이썬에서 재현.
  - answer_index = hashStr(date_string + COMANTLE_SALT) % len(order)
    (보안: 솔트는 서버 환경변수에만 둔다. djb2 가 공개돼도 정답 인덱스를 재현 못 하게 한다.)
  - /api/today 응답 함수 목록은 id 사전순으로 셔플 — functions.json 순서(self.order)는 불변.
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("comantle")

try:
    # Asia/Seoul 을 정확히 쓰기 위해 zoneinfo(+tzdata) 사용.
    from zoneinfo import ZoneInfo
    _SEOUL = ZoneInfo("Asia/Seoul")
except Exception:  # tzdata 가 없는 환경 대비 — KST 는 DST 가 없으므로 고정 +9 로 안전하게 폴백.
    _SEOUL = timezone(timedelta(hours=9))

# 데일리 정답 기준 타임존(상수로 명시) — Asia/Seoul 자정 기준.
GAME_TZ = _SEOUL

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
FUNCTIONS_PATH = os.path.join(DATA_DIR, "functions.json")
SCORES_PATH = os.path.join(DATA_DIR, "scores.json")

# top100(정답 기준 유사도 상위 목록) 의 크기.
TOP_N = 100


def hash_str(s: str) -> int:
    """프론트 app.js 의 djb2 와 동일 알고리즘 (부호 없는 32비트).

    JS:  let h = 5381; h = ((h << 5) + h + s.charCodeAt(i)) >>> 0;
    날짜 문자열은 모두 ASCII 이므로 ord(c) == charCodeAt(i) 가 보장된다.
    """
    h = 5381
    for ch in s:
        h = ((h << 5) + h + ord(ch)) & 0xFFFFFFFF
    return h


class Game:
    """functions.json + scores.json 을 메모리에 로드해 룩업/정답 결정을 담당."""

    def __init__(self, functions_path: str = FUNCTIONS_PATH, scores_path: str = SCORES_PATH):
        with open(functions_path, "r", encoding="utf-8") as f:
            self.functions = json.load(f)
        with open(scores_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        # scores.json 은 {meta, scores} 구조. 룩업 대상은 inner scores.
        self.scores = raw["scores"] if isinstance(raw, dict) and "scores" in raw else raw

        ids = [fn["id"] for fn in self.functions]
        if len(set(ids)) != len(ids):
            raise SystemExit("functions.json 에 중복된 id 가 있습니다.")
        self.by_id = {fn["id"]: fn for fn in self.functions}
        self.order = ids  # 정답 인덱싱 기준 순서(= functions.json 순서). 셔플과 무관하게 절대 불변.

        # 보안: 정답 계산에 섞는 서버 비밀(솔트). 환경변수에만 두고 코드/클라엔 없다.
        # 기본 비밀값을 하드코딩하지 않는다(코드가 새면 무의미). 미설정이면 빈 솔트(=기존 동작)로
        # 동작하되 기동 시 눈에 띄게 경고한다.
        self.salt = os.environ.get("COMANTLE_SALT", "")
        if not self.salt:
            # ASCII 전용 — 콘솔 코드페이지(cp949 등)와 무관하게 print 가 절대 깨지지 않도록.
            msg = ("[SECURITY] COMANTLE_SALT is not set - answer-prediction defense is OFF "
                   "(empty salt = legacy behavior). Set a long random string before production.")
            logger.warning(msg)
            print(msg, file=sys.stderr)  # uvicorn 로깅 설정과 무관하게 반드시 보이도록

        # 각 정답별 '점수 내림차순 순위 맵'을 기동 시 미리 계산 (정답 자신 = 1위).
        # 점수표를 클라에 내리지 않고도 서버가 순위 숫자만 내려주기 위한 것(§ 진행 중 순위 노출).
        self.rank_maps = self._build_rank_maps()

    # ---- 날짜 / 정답 결정 (§1) -------------------------------------------------
    def today_str(self) -> str:
        """게임 기준 타임존(Asia/Seoul)의 오늘 날짜 'YYYY-MM-DD'."""
        return datetime.now(GAME_TZ).strftime("%Y-%m-%d")

    def answer_id_for(self, date_str: str) -> str:
        """날짜 문자열(+서버 솔트)로 그날의 공통 정답 id 를 결정론적으로 계산.

        보안(§1): djb2 는 프론트에도 있어 공개돼 있다. 솔트를 섞어 'hash(date) % len' 으로는
        인덱스를 재현하지 못하게 한다. 솔트는 정답 결정에만 쓰고, self.order 는 건드리지 않는다.
        """
        idx = hash_str(date_str + self.salt) % len(self.order)
        return self.order[idx]

    # ---- 클라 비공개 정보가 새지 않는 '메타 목록' (§2 /api/today) --------------
    def public_functions(self):
        """프론트 자동완성/동명이의용 최소 메타. 점수·정답·library·description 노출 금지.

        보안(§2): 응답 순서를 functions.json 순서(= self.order)와 무관하게 id 사전순으로 낸다.
        functions.json 순서가 드러나지 않으면 'hash(date) % len' 인덱스를 함수로 매핑할 수 없다.
        정렬은 '응답'에만 적용 — 내부 정답 인덱싱(self.order)은 절대 바꾸지 않는다. 결정적 순서라
        캐싱·테스트에도 안전하다.
        """
        funcs_sorted = sorted(self.functions, key=lambda fn: fn["id"])
        return [
            {
                "id": fn["id"],
                "displayName": fn["displayName"],
                "aliases": fn.get("aliases", []),
                "language": fn["language"],
            }
            for fn in funcs_sorted
        ]

    # ---- 순위 (진행 중 노출, 서버 계산) ---------------------------------------
    def _rank_sort_key(self, item):
        # 점수 내림차순, 동점이면 displayName 사전순 → top100 정렬과 동일(순위 일관성).
        gid, score = item
        return (-score, self.by_id[gid]["displayName"])

    def _build_rank_maps(self):
        """{answerId: {guessId: rank}} — 정답을 제외하고 점수 내림차순 1위부터.

        가장 가까운 '비정답'이 1위. 정답 자신은 순위 숫자가 아니라 '정답'으로 다루므로 None.
        scores.json 이 작아(정답×함수) 전수 선계산해도 부담 없다.
        """
        maps = {}
        for aid, row in self.scores.items():
            non_answer = [(gid, s) for gid, s in row.items() if gid != aid]
            non_answer.sort(key=self._rank_sort_key)
            m = {gid: i + 1 for i, (gid, _) in enumerate(non_answer)}
            m[aid] = None  # 정답 자신 = '정답'(순위 숫자 아님)
            maps[aid] = m
        return maps

    def rank_of(self, answer_id: str, function_id: str):
        """정답 기준, 추측 함수의 유사도 순위(정수, 가장 가까운 비정답=1위).

        정답 자신이거나 점수표에 없으면 None. 순위 '숫자 하나'만 노출한다.
        """
        return self.rank_maps.get(answer_id, {}).get(function_id)

    # ---- 점수 룩업 (§2 /api/guess) --------------------------------------------
    def has_function(self, function_id: str) -> bool:
        return function_id in self.by_id

    def score_of(self, answer_id: str, function_id: str):
        """scores[answer_id][function_id] 룩업. 없으면 None (점수 0 을 흘리지 않기 위해)."""
        row = self.scores.get(answer_id)
        if row is None:
            return None
        return row.get(function_id)

    # ---- 정답 공개 정보 (정답 맞힘/포기 후에만 main.py 가 호출) ----------------
    def reveal_answer(self, answer_id: str):
        """정답이 드러나는 정보. 반드시 종료(맞힘/포기) 후에만 응답에 실어야 한다."""
        fn = self.by_id[answer_id]
        lib = fn.get("library") or ""
        return {
            "id": fn["id"],
            "displayName": fn["displayName"],
            "language": fn["language"],
            "library": lib if lib.strip() else "라이브러리 없음",
            "description": fn.get("description", ""),
        }

    def hint(self, answer_id: str, level: int):
        """진행 중 힌트(§4 결정): level 1 = 정답 언어, level 2 = 정답 라이브러리.

        주의(부정 방지 약화): 서버는 상태가 없으므로 이 정보는 '맞히기/포기 전'에도 누구나
        받을 수 있다. 노출은 '정답의 언어/라이브러리'로 제한되며(정답 id·top100 없음),
        포기(giveup)보다 적게 새는, 설계상 허용된 부분 노출로 간주한다.
        """
        fn = self.by_id[answer_id]
        if level == 1:
            return {"level": 1, "language": fn["language"]}
        if level == 2:
            lib = fn.get("library") or ""
            return {"level": 2, "library": lib if lib.strip() else "라이브러리 없음"}
        return None

    def _top_entry(self, gid: str, rank, score, is_answer: bool):
        fn = self.by_id[gid]
        lib = fn.get("library") or ""
        return {
            "rank": rank,  # 정답은 None('정답'), 비정답은 1위부터의 정수
            "id": gid,
            "displayName": fn["displayName"],
            "language": fn["language"],
            "library": lib if lib.strip() else "라이브러리 없음",
            "score": score,
            "isAnswer": is_answer,
        }

    def top100(self, answer_id: str):
        """정답 포함 목록: 맨 위 '정답'(순위 아님) + 가장 가까운 비정답 1위..TOP_N위.

        정답을 제외하고 매긴 순위라 '100위까지' 모두 보이고, 정답은 별도로 함께 포함된다.
        정답이 통째로 드러나는 데이터이므로 종료(맞힘/포기) 후에만 응답에 실어야 한다(§0.4).
        """
        row = self.scores.get(answer_id, {})
        non_answer = [(gid, s) for gid, s in row.items() if gid != answer_id]
        non_answer.sort(key=self._rank_sort_key)
        ranked = non_answer[:TOP_N]

        out = [self._top_entry(answer_id, None, row.get(answer_id, 100.0), True)]
        for rank, (gid, score) in enumerate(ranked, start=1):
            out.append(self._top_entry(gid, rank, score, False))
        return out
