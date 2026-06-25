# 코맨틀 (Comantle) — 부정 방지 백엔드 + 프론트

함수의 의미(설명문 임베딩 유사도)로 오늘의 정답 함수를 좁혀 맞히는 게임.
**점수와 정답은 서버가 쥐고, 클라이언트는 표시만 한다.**

## 부정 방지 핵심 (요약)

- **점수표(scores.json)는 클라로 내려보내지 않는다.** 서버가 메모리에 올려 룩업만 한다.
  (임베딩 런타임 호출 없음 — 사전계산 점수 룩업만.)
- **정답 여부는 서버가 판정**한다. 프론트는 `/api/guess` 의 `correct` 플래그만 신뢰한다.
- **정답 단서(정답 id·언어·라이브러리·top100)**는 맞히기/포기/힌트 응답으로만 나간다.
- **진행 중 유사도 순위는 표시하지 않는다.** 종료(맞힘/포기) 후 top100 으로만 채운다.
- 유저 상태는 서버에 저장하지 않는다(DB 없음). 진행 기록은 브라우저 localStorage 에만.

## 디렉터리

```
comantle/
  backend/            # FastAPI (서버 전용 데이터 포함)
    main.py           # 엔드포인트
    game.py           # 날짜 시드 정답 결정 + 점수 룩업
    data/             # functions.json, scores.json (클라 비공개)
    requirements.txt  Dockerfile
  frontend/           # 정적 프론트 (data.js 제거, API 연동)
    index.html  app.js  style.css
  build_scores.py     # 점수표 생성 파이프라인 (그대로 유지)
  sync_data.py        # 루트 산출물 -> backend/data 동기화
  docker-compose.yml  # 로컬 백엔드 기동(선택)
```

## 로컬 실행

1) 백엔드 (택1)

```bash
# (a) 고정 환경값(.env)으로 기동 — 권장 (COMANTLE_SALT 등을 매번 같은 값으로)
cd backend
py -3.13 -m pip install -r requirements.txt
.\run.ps1        # Windows PowerShell  (bash: ./run.sh)

# (b) 직접 (환경변수 수동 지정)
cd backend
COMANTLE_SALT=... COMANTLE_DEV=1 py -3.13 -m uvicorn main:app --host 127.0.0.1 --port 8000

# (c) docker  (backend/.env 를 env_file 로 읽음)
docker compose up --build
```

> `backend/.env` 가 `COMANTLE_SALT`(고정 솔트)·`COMANTLE_DEV` 등을 담는 단일 출처다.
> `run.ps1`/`run.sh` 가 이 파일을 읽어 환경변수로 올린 뒤 서버를 띄운다 — 값을 매번 새로
> 만들지 않고 항상 같은 값을 쓴다. 운영(Render)에서는 `.env` 대신 플랫폼 환경변수로 설정할 것.

2) 프론트 (정적 서버 — 5173 은 CORS 허용 목록에 포함)

```bash
cd frontend
py -3.13 -m http.server 5173 --bind 127.0.0.1
# 브라우저: http://127.0.0.1:5173/index.html
```

> `frontend/index.html` 의 `window.COMANTLE_API_BASE` 가 백엔드 주소다.
> 같은 출처로 서빙하면 `""`(빈 문자열)로 두면 된다.

## 환경변수

| 변수 | 설명 | 기본 |
|---|---|---|
| `COMANTLE_ALLOWED_ORIGINS` | CORS 허용 출처(콤마 구분) | localhost:5173/8080 |
| `COMANTLE_DEV` | `1` 이면 `?date=YYYY-MM-DD` 날짜 덮어쓰기 허용(테스트용) | 꺼짐 |
| `COMANTLE_RANK_CUTOFF` | 양의 정수면 그 순위 밖은 `rank:null`(순위권 밖)로 가림 | 무제한 |
| `COMANTLE_SALT` | 정답 계산에 섞는 서버 비밀(긴 무작위 문자열). 미설정 시 기동 경고 + 예측 방지 비활성 | 빈 값 |

> ⚠️ **`COMANTLE_SALT`**: djb2 해시가 프론트에 공개돼 있어, 솔트 없이는 `hash(날짜) % 269` 로
> 미래 정답을 미리 계산할 수 있다. 솔트를 서버 환경변수로만 두면(코드·git 금지) 정답 인덱스를
> 재현할 수 없다. **한 번 정하면 고정** — 바꾸면 그날 정답이 바뀌어 진행 중 판이 깨진다.
> 추가로 `/api/today` 함수 목록은 id 사전순으로 내려가 functions.json 순서도 드러나지 않는다.

## 정답 결정 (날짜 시드, 저장 안 함)

`answer_index = djb2(date_string) % len(FUNCTIONS)`,
날짜 = Asia/Seoul 자정 기준 당일. djb2 는 프론트 기존 구현(부호 없는 32비트)과 동일 알고리즘을
파이썬에서 재현 → 같은 날 모두 같은 정답.

## API

- `GET  /api/today` → `{date, functions:[{id,displayName,aliases,language}]}` (점수·정답·library 없음)
- `POST /api/guess  {date, functionId}` → `{score, correct, rank}` (+ 정답일 때만 `answer`, `top100`)
  - `rank` = 그날 정답 기준 유사도 순위(정수). **가장 가까운 비정답 = 1위**, 정답 자신은 순위가
    아니라 '정답'이라 `rank: null`. 서버가 계산해 **숫자 하나만** 내려준다(점수표·정답 분포 동반
    금지). `COMANTLE_RANK_CUTOFF` 설정 시 그 밖은 `null`.
  - `top100`(종료 후): 맨 위 정답(`isAnswer:true`, `rank:null`) + 가장 가까운 비정답 `1위..100위`.
- `POST /api/giveup {date}` → `{answer, top100}` (설계상 허용된 노출)
- `POST /api/hint   {date, level}` → level 1=언어, 2=라이브러리만

> ⚠️ 힌트/포기는 서버에 상태가 없어 '맞히기 전'에도 호출 가능하다. 노출은 그날 정답 하나
> (+top100)로 제한되며 점수표 전체는 새지 않는다. 더 강한 방지(세션/레이트리밋)는 DB·상태가
> 필요해 이번 범위 밖이다.

## 개발 모드 / "새 게임" 버튼

자정을 기다리지 않고 다른 정답으로 새 판을 시작하는 **개발 전용** 기능.

- 동작 원리: 프론트가 임의의 **가짜 날짜**(유효한 ISO 날짜)를 만들어 그 판의 모든 요청
  (`/api/today`·`/api/guess`·…)에 실어 보낸다. 서버가 그 날짜로 새 정답을 뽑는다. **정답 id 를
  직접 지정하지 않는다** — 날짜만 바꾼다. 한 판 안에서는 같은 가짜 날짜를 유지(날짜 일관성).
- **게이팅**: 서버 `COMANTLE_DEV` 가 켜져 있을 때만 날짜 덮어쓰기가 먹힌다. 꺼져 있으면 서버가
  날짜를 **완전히 무시**하고 항상 오늘(KST)만 쓴다 → 새 게임은 무의미해지고, 프론트도 `/api/today`
  의 `dev:false` 를 보고 버튼을 숨긴다.
- **출시 시**: ① 서버 `COMANTLE_DEV` 끄기(최종 방어선) + ② `app.js` 의 `╔ 개발 모드 전용 블록 ╗`
  하나만 제거. 둘 중 하나만 해도 날짜 덮어쓰기가 막힌다.

## 점수표 갱신

```bash
py -3.13 build_scores.py   # functions.json -> scores.json (검증 포함)
py -3.13 sync_data.py      # 루트 scores.json/functions.json -> backend/data
```
