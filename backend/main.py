# -*- coding: utf-8 -*-
"""
main.py — 코맨틀 부정 방지 백엔드 (FastAPI, DB 없음).

존재 이유: 점수표와 정답을 '서버가' 쥐고, 클라이언트는 표시만 한다.
  - GET  /api/today           : 그날 게임용 최소 메타 (점수·정답·library 없음)
  - POST /api/guess           : 점수 + correct 판정. 정답일 때만 정답 공개 + top100
  - POST /api/giveup          : 정답 공개 + top100 (설계상 허용된 노출, §3)

서버는 유저 상태를 저장하지 않는다(§0.5). 정답 공개는 서버가 '그 자리에서 검증 가능한 경우'
(정답을 맞힌 guess) 또는 '포기 요청'(본질적으로 정답을 달라는 요청, §3)에만 이뤄진다.
"""

import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from game import Game

app = FastAPI(title="Comantle Backend", version="1.0")

# ---- CORS (§2): 프론트 출처를 환경변수로 허용 목록 관리 ----------------------
# COMANTLE_ALLOWED_ORIGINS="http://localhost:5173,https://comantle.vercel.app"
_default_origins = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080,http://127.0.0.1:8080"
ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get("COMANTLE_ALLOWED_ORIGINS", _default_origins).split(",") if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ---- 개발 전용 날짜 덮어쓰기 플래그 (§1) ------------------------------------
# COMANTLE_DEV=1 일 때만 ?date=YYYY-MM-DD 덮어쓰기를 허용. 정답 노출 용도가 아니라
# 테스트 편의(특정 날짜의 판 재현)를 위한 것. 기본 운영에서는 꺼둔다.
DEV_MODE = os.environ.get("COMANTLE_DEV", "") in ("1", "true", "True")


def _env_positive_int(name: str):
    """양의 정수 환경변수 파싱. 미설정/0/비정상은 None(=무제한)."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        n = int(raw)
    except ValueError:
        return None
    return n if n > 0 else None


# ---- 진행 중 순위 노출 컷오프 (상위 100위까지만) ----------------------------
# "상위 N위만 실제 순위, 그 밖은 순위권 밖(rank=None)"으로 가리는 단일 지점.
# 기본 100. 환경변수 COMANTLE_RANK_CUTOFF 로 덮어쓸 수 있다(양의 정수만 인정).
RANK_CUTOFF = _env_positive_int("COMANTLE_RANK_CUTOFF") or 100  # 기본 100위 컷오프


def _apply_rank_cutoff(rank):
    """rank 를 반환하기 직전의 유일한 컷오프 적용 지점.

    컷오프가 설정돼 있고 rank 가 그보다 크면 None('순위권 밖')으로 가린다.
    RANK_CUTOFF=100 이라 101위 이하는 rank=None 으로 나간다.
    """
    if rank is None:
        return None
    if RANK_CUTOFF is not None and rank > RANK_CUTOFF:
        return None
    return rank


game = Game()


def _resolve_date(date_str: str | None) -> str:
    """요청 날짜를 결정. 개발 모드에서만 명시 날짜 허용, 아니면 서버 기준 오늘."""
    if date_str:
        if not DEV_MODE:
            # 운영에서는 임의 날짜 지정 무시 — 항상 서버 기준 오늘로 강제.
            return game.today_str()
        _validate_date(date_str)
        return date_str
    return game.today_str()


def _validate_date(date_str: str) -> None:
    import datetime as _dt

    try:
        _dt.date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="잘못된 날짜 형식입니다. YYYY-MM-DD 를 사용하세요.")


# ---- 요청 모델 --------------------------------------------------------------
class GuessBody(BaseModel):
    date: str
    functionId: str


class GiveUpBody(BaseModel):
    date: str


class HintBody(BaseModel):
    date: str
    level: int  # 1 = 언어, 2 = 라이브러리


# ---- 엔드포인트 -------------------------------------------------------------
@app.get("/api/today")
def today(date: str | None = Query(default=None)):
    """그날 게임 식별용 최소 정보. 정답 id·점수·library·description 노출 금지."""
    date_str = _resolve_date(date)
    return {
        "date": date_str,
        # 개발 모드 여부(프론트의 '새 게임' 버튼 노출/동작 게이팅용). 정답 단서가 아니라
        # 서버 설정 플래그일 뿐이다. false 면 프론트가 날짜 덮어쓰기를 시도해도 위 _resolve_date
        # 가 무시하고 오늘을 돌려준다(부정 방지 최종 방어선).
        "dev": DEV_MODE,
        "functions": game.public_functions(),
    }


@app.post("/api/guess")
def guess(body: GuessBody):
    """날짜로 정답 계산 → scores[answerId][functionId] 룩업.

    반환: {score, correct}. 정답일 때만 정답 공개 정보 + top100 동반.
    오답이면 절대 정답 단서 없음. 미등록 functionId 는 4xx (점수 0 을 흘리지 않음).
    """
    date_str = _resolve_date(body.date)

    if not game.has_function(body.functionId):
        raise HTTPException(status_code=400, detail="등록되지 않은 함수입니다.")

    answer_id = game.answer_id_for(date_str)
    score = game.score_of(answer_id, body.functionId)
    if score is None:
        # 점수표에 행/열이 없는 비정상 케이스 — 0 을 흘리지 말고 에러.
        raise HTTPException(status_code=400, detail="점수를 찾을 수 없습니다.")

    correct = body.functionId == answer_id
    # 순위는 서버가 계산해 '숫자 하나'만 내려준다(점수표·정답 분포는 동반 금지).
    # 가장 가까운 비정답 = 1위. 정답 자신은 순위가 아니라 '정답'이므로 rank 는 None 으로 나간다.
    rank = _apply_rank_cutoff(game.rank_of(answer_id, body.functionId))
    resp = {"score": score, "correct": correct, "rank": rank, "date": date_str}

    if correct:
        # 서버가 '그 자리에서' 정답임을 확인했으므로 공개는 안전(§3).
        resp["answer"] = game.reveal_answer(answer_id)
        resp["top100"] = game.top100(answer_id)

    return resp


@app.post("/api/giveup")
def giveup(body: GiveUpBody):
    """포기 — 본질적으로 '정답을 달라'는 요청(§3). 정답 공개 + top100 을 반환.

    서버는 상태를 저장하지 않으므로 '진짜 포기했는지'를 검증할 수 없다. 이는 설계상
    허용된 노출이며, 새는 것은 '그날 정답 하나 + top100'으로 제한된다(점수표 전체 아님).
    """
    date_str = _resolve_date(body.date)
    answer_id = game.answer_id_for(date_str)
    return {
        "date": date_str,
        "answer": game.reveal_answer(answer_id),
        "top100": game.top100(answer_id),
    }


@app.post("/api/hint")
def hint(body: HintBody):
    """진행 중 힌트(§4 결정): level 1 = 정답 언어, level 2 = 정답 라이브러리만 반환.

    부정 방지 약화 인지: 서버에 상태가 없어 '맞히기/포기 전'에도 호출 가능하다. 노출은
    정답의 언어/라이브러리로 제한되며(정답 id·점수표·top100 없음), giveup 보다 적게 새는
    설계상 허용된 부분 노출이다. 더 강한 방지는 세션/상태가 필요 → 범위 밖(§3).
    """
    if body.level not in (1, 2):
        raise HTTPException(status_code=400, detail="잘못된 힌트 레벨입니다. (1 또는 2)")
    date_str = _resolve_date(body.date)
    answer_id = game.answer_id_for(date_str)
    return {"date": date_str, **game.hint(answer_id, body.level)}


@app.api_route("/api/health", methods=["GET", "HEAD"])
def health():
    return {"status": "ok", "functions": len(game.functions), "dev": DEV_MODE}
