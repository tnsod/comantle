/* 코맨틀 — 게임 런타임 (A안: 점수·정답은 서버가 쥔다).
 *
 * 부정 방지 계약(§0):
 *   - 점수표(scores.json)는 클라로 내려오지 않는다. 점수는 매 추측마다 POST /api/guess 로 받는다.
 *   - 정답 여부는 서버 응답 correct 플래그만 신뢰한다 (클라가 "100점=정답"으로 추론하지 않음).
 *   - 정답 단서(정답 id·언어·라이브러리·top100)는 맞히기/포기/힌트 응답으로만 들어온다.
 *   - 유사도 순위는 서버가 추측마다 'rank 숫자 하나'로 내려준다(진행 중에도 노출). 클라는
 *     점수표 전체를 모른 채 그 숫자만 표시한다. 종료 후 top100 은 별도로 서버가 줄 때만 표시.
 *
 * 진행 기록(§5)은 localStorage(comantle:<date>)에만. 점수표 전체·정답 id 는 저장하지 않는다.
 *
 * 입력 방식: 자유 입력 + alias 매칭 (타이핑 자동완성 없음 — 브라우징 방지).
 *   - alias 정확히 1개 → 바로 채점
 *   - 0개 → 미등록 안내(시도 차감 없음)
 *   - 2개 이상(동명이의) → 이때만 후보 선택 UI 노출
 */
(function () {
  "use strict";

  // ---- API ----
  const API_BASE = (window.COMANTLE_API_BASE || "").replace(/\/+$/, "");
  const apiUrl = (path) => API_BASE + path;

  async function apiPost(path, body) {
    const res = await fetch(apiUrl(path), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      let detail = "요청 처리 중 오류가 발생했어요.";
      try {
        const j = await res.json();
        if (j && j.detail) detail = j.detail;
      } catch (_) {}
      const err = new Error(detail);
      err.status = res.status;
      throw err;
    }
    return res.json();
  }

  // ---- 함수 메타 (서버 /api/today 에서 로드. 점수·정답·library 없음) ----
  let FUNCTIONS = [];
  const byId = {};
  // alias(소문자) -> [id, ...]  (동명이의 함수는 같은 alias 에 여러 id)
  const aliasIndex = {};

  function buildIndices() {
    FUNCTIONS.forEach((fn) => { byId[fn.id] = fn; });
    FUNCTIONS.forEach((fn) => {
      const keys = new Set([fn.displayName, ...(fn.aliases || [])].map((a) => a.toLowerCase()));
      keys.forEach((k) => {
        if (!aliasIndex[k]) aliasIndex[k] = [];
        if (!aliasIndex[k].includes(fn.id)) aliasIndex[k].push(fn.id);
      });
    });
  }

  // ---- 상태 ----
  const state = {
    date: null,
    answerId: null,          // 종료(맞힘/포기) 후 서버가 알려줄 때까지 null
    attempts: [],            // [{ id, score }]
    guessed: new Set(),
    latestId: null,          // 가장 최근 추측 id (상단에 따로 표시)
    solved: false,           // 정답을 맞힘 (입력은 계속 가능)
    gaveUp: false,           // 포기함 (입력은 계속 가능)
    hintLangUsed: false,
    hintLibUsed: false,
    hintLang: null,          // 서버가 준 힌트 값 (메모리 전용 — localStorage 에는 저장 안 함)
    hintLib: null,
    reveal: null,            // 종료 후 서버 정답 공개 {id,displayName,language,library,description}
    top100: null,            // 종료 후 서버 top100 배열
    devMode: false,          // 서버 DEV_MODE 여부 (개발 전용 '새 게임' 버튼 게이팅)
  };

  const isEnded = () => state.solved || state.gaveUp;

  // ---- 유사도 순위: 서버가 추측마다 'rank 숫자 하나'를 내려준다(진행 중에도 노출).
  // 클라는 점수표 전체를 모른다 — 순위 계산을 클라가 하지 않는다(§0 유지).
  function rankLabel(attempt) {
    if (state.answerId && attempt.id === state.answerId) return "정답";
    const r = attempt.rank;
    if (r === null) return "순위권 밖";        // 컷오프 적용 시(현재 서버는 항상 숫자를 줌)
    if (r === undefined) return "—";          // 순위 정보가 없는 경우(이전 버전 기록 등)
    return `${r}위`;
  }

  // ---- localStorage (진행 기록만 — 점수표 전체·정답 id 저장 금지) ----
  function storageKey() { return "comantle:" + state.date; }

  function saveProgress() {
    try {
      const payload = {
        date: state.date,
        attempts: state.attempts.map((a) => ({ functionId: a.id, score: a.score, rank: a.rank })),
        solved: state.solved,
        gaveUp: state.gaveUp,
        hintLangUsed: state.hintLangUsed,
        hintLibUsed: state.hintLibUsed,
      };
      localStorage.setItem(storageKey(), JSON.stringify(payload));
    } catch (_) { /* 사적 모드 등에서 localStorage 실패는 조용히 무시 */ }
  }

  function loadRawProgress() {
    try {
      const raw = localStorage.getItem(storageKey());
      if (!raw) return null;
      const p = JSON.parse(raw);
      if (!p || p.date !== state.date) return null; // 날짜가 바뀌면 이전 기록 무시(새 판)
      return p;
    } catch (_) { return null; }
  }

  // ---- DOM ----
  const $ = (id) => document.getElementById(id);
  const input = $("guessInput");
  const candidateBox = $("suggestions"); // 동명이의 후보 선택 전용
  const messageEl = $("message");
  const attemptTable = $("attemptTable");
  const attemptCount = $("attemptCount");
  const hintBox = $("hintBox");

  // ---- HTML 이스케이프 ----
  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ESC[c]); }

  // ---- 점수 막대 색상 ----
  function scoreColor(score) {
    const hue = Math.round((score / 100) * 120); // 0=빨강 ~ 120=초록
    return `hsl(${hue}, 70%, 50%)`;
  }

  // ---- 동명이의 후보 선택 UI (언어 배지만) ----
  function hideCandidates() {
    candidateBox.classList.add("hidden");
    candidateBox.innerHTML = "";
  }

  function showCandidates(ids) {
    candidateBox.innerHTML = "";
    ids.forEach((id) => {
      const fn = byId[id];
      const li = document.createElement("li");
      const guessed = state.guessed.has(id) ? " · 이미 추측" : "";
      li.innerHTML =
        `<span class="s-name">${esc(fn.displayName)}${guessed}</span>` +
        `<span class="s-meta"><span class="lang-badge">${esc(fn.language)}</span></span>`;
      li.addEventListener("mousedown", (e) => {
        e.preventDefault(); // blur 전에 선택
        submitGuess(id);
      });
      candidateBox.appendChild(li);
    });
    candidateBox.classList.remove("hidden");
  }

  // ---- 추측 처리 ----
  function setMessage(msg) { messageEl.textContent = msg || ""; }

  function tryTextGuess(text) {
    const q = text.trim().toLowerCase();
    if (!q) return;
    const ids = aliasIndex[q];
    if (!ids || ids.length === 0) {
      setMessage("등록되지 않은 함수예요. (시도에 포함되지 않아요)");
      return;
    }
    if (ids.length === 1) {
      submitGuess(ids[0]);
      return;
    }
    setMessage("같은 이름의 함수가 여러 개예요. 아래에서 골라주세요.");
    showCandidates(ids);
  }

  async function submitGuess(id) {
    const fn = byId[id];
    if (!fn) { setMessage("등록되지 않은 함수예요. (시도에 포함되지 않아요)"); return; }

    // 중복 입력 방지 (정답을 맞힌 뒤에도 유지)
    if (state.guessed.has(id)) {
      setMessage(`이미 추측한 함수예요: ${fn.displayName}`);
      input.value = "";
      hideCandidates();
      return;
    }

    input.value = "";
    hideCandidates();
    setMessage("");

    let resp;
    try {
      resp = await apiPost("/api/guess", { date: state.date, functionId: id });
    } catch (e) {
      setMessage(e.message || "점수를 받지 못했어요. 잠시 후 다시 시도해주세요.");
      return;
    }

    // 점수·순위 출처는 서버. 정답 판정도 서버 correct 플래그만 신뢰.
    state.attempts.push({ id, score: resp.score, rank: resp.rank });
    state.guessed.add(id);
    state.latestId = id;

    if (resp.correct) {
      state.solved = true; // 상태만 저장, 입력은 막지 않음
      if (resp.answer) {
        state.answerId = resp.answer.id;
        state.reveal = resp.answer;
      }
      if (resp.top100) {
        state.top100 = resp.top100;
      }
    }

    saveProgress();
    renderAttempts();
    refreshEndUI();
  }

  // ---- 결과 렌더 ----
  function rowHtml(attempt, classes) {
    const fn = byId[attempt.id];
    const color = scoreColor(attempt.score);
    const cls = ["row"].concat(classes || []);
    if (state.answerId && attempt.id === state.answerId) cls.push("correct");
    return (
      `<div class="${cls.join(" ")}">` +
      `<span class="c-rank">#${attempt.order + 1}</span>` +
      `<span class="c-word">${esc(fn.displayName)} <span class="c-lang">${esc(fn.language)}</span></span>` +
      `<span class="c-score">${attempt.score.toFixed(1)}</span>` +
      `<span class="c-srank">${rankLabel(attempt)}</span>` +
      `<span class="c-bar"><i style="width:${attempt.score}%;background:${color}"></i></span>` +
      `</div>`
    );
  }

  function renderAttempts() {
    attemptCount.textContent = `시도 ${state.attempts.length}회`;

    if (!state.attempts.length) {
      attemptTable.innerHTML = "";
      return;
    }

    const rows = state.attempts.map((a, i) => ({ ...a, order: i }));
    const recent = rows.find((r) => r.id === state.latestId) || rows[rows.length - 1];
    const others = rows
      .filter((r) => r.id !== recent.id)
      .sort((a, b) => b.score - a.score || a.order - b.order);

    const header =
      `<div class="row head">` +
      `<span class="c-rank">#</span>` +
      `<span class="c-word">추측한 함수</span>` +
      `<span class="c-score">유사도</span>` +
      `<span class="c-srank">유사도 순위</span>` +
      `<span class="c-bar">그래프</span>` +
      `</div>`;

    let html = header + rowHtml(recent, ["recent"]);
    if (others.length) {
      html += `<div class="divider"></div>`;
      html += others.map((r) => rowHtml(r, [])).join("");
    }
    attemptTable.innerHTML = html;
  }

  // ---- 게임 종료 UI (정답 / 포기 공통) — 서버가 준 정답 공개 정보 사용 ----
  function libLabel(rev) {
    return rev && rev.library ? rev.library : "라이브러리 없음";
  }

  function renderReveal() {
    const rev = state.reveal;
    if (!rev) return; // 정답 공개 정보가 아직 없으면 그리지 않음(이론상 종료 상태에선 항상 있음)
    const box = $("winBox");
    // 포기가 먼저면 그 뒤에 정답을 맞혀도 '정답' 박스로 바뀌지 않는다(포기 화면 유지).
    const solvedView = state.solved && !state.gaveUp;
    box.classList.toggle("gaveup", !solvedView);

    const head = solvedView ? "🎉 정답!" : "포기했어요";
    let summary;
    if (solvedView) {
      const best = Math.max(0, ...state.attempts
        .filter((a) => a.id !== state.answerId)
        .map((a) => a.score));
      summary = `시도 횟수 <b>${state.attempts.length}회</b> · 정답 전 최고 유사도 <b>${best.toFixed(1)}점</b>`;
    } else {
      summary = "계속 입력하며 다른 함수들의 유사도도 확인할 수 있어요.";
    }

    box.innerHTML =
      `<h2>${head}</h2>` +
      `<div class="reveal-line">정답: <span class="answer-name">${esc(rev.displayName)}</span></div>` +
      `<div class="reveal-line">언어: <b>${esc(rev.language)}</b></div>` +
      `<div class="reveal-line">라이브러리: <b>${esc(libLabel(rev))}</b></div>` +
      `<div class="win-desc">${esc(rev.description || "")}</div>` +
      `<div class="win-summary">${summary}</div>` +
      `<div class="reveal-actions" style="margin-top:14px">` +
      `<button type="button" id="openTop100Btn" class="hint-btn">유사도 100위 보기 ↗</button></div>`;
    box.classList.remove("hidden");

    const btn = $("openTop100Btn");
    if (btn) btn.addEventListener("click", openTop100Window);
  }

  // 정답과 가장 유사한 함수 top100 을 새 창에 띄운다 (서버가 준 정렬·순위 그대로).
  function openTop100Window() {
    const rev = state.reveal;
    const list = state.top100 || [];
    if (!rev || !list.length) { setMessage("100위 목록을 아직 받지 못했어요."); return; }

    const rankedCount = list.filter((x) => !x.isAnswer).length;
    const rowsHtml = list.map((item) => {
      const cls = item.isAnswer ? ' class="ans"' : "";
      const rankCell = item.isAnswer ? "정답" : item.rank;
      return `<tr${cls}><td class="r">${rankCell}</td><td class="n">${esc(item.displayName)}</td>` +
        `<td class="l">${esc(item.language)}</td><td class="lib">${esc(item.library || "라이브러리 없음")}</td>` +
        `<td class="s">${item.score.toFixed(1)}</td></tr>`;
    }).join("");

    const doc =
      `<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">` +
      `<meta name="viewport" content="width=device-width, initial-scale=1.0">` +
      `<title>유사도 100위 — ${esc(rev.displayName)}</title><style>` +
      `:root{--bg:#fff;--paper:#F7F8FB;--ink:#1B2231;--muted:#8B93A6;--line:#E5E9F1;--green:#1AA85C;--win:#E9F8F0;}` +
      `*{box-sizing:border-box;}body{margin:0;background:var(--bg);color:var(--ink);` +
      `font-family:"Pretendard",system-ui,"Malgun Gothic",sans-serif;font-size:13px;}` +
      `.hd{position:sticky;top:0;background:var(--bg);padding:14px 16px 10px;border-bottom:1px solid var(--line);}` +
      `.hd h1{margin:0;font-size:15px;}.hd p{margin:4px 0 0;color:var(--muted);font-size:12px;}` +
      `.hd b{color:var(--green);}table{width:100%;border-collapse:collapse;}` +
      `th,td{padding:7px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap;}` +
      `th{position:sticky;top:55px;background:var(--paper);color:var(--muted);font-size:11px;}` +
      `td.r,td.s,th.r,th.s{text-align:right;font-variant-numeric:tabular-nums;}` +
      `td.n{font-weight:700;}td.l,td.lib{color:var(--muted);}td.s{font-weight:800;}` +
      `tr.ans td{background:var(--win);}tr.ans td.n{color:var(--green);}` +
      `</style></head><body>` +
      `<div class="hd"><h1>정답과 가장 가까운 함수 ${rankedCount}개 + 정답</h1>` +
      `<p>정답: <b>${esc(rev.displayName)}</b> · ${esc(rev.language)} · ${esc(libLabel(rev))}</p></div>` +
      `<table><thead><tr><th class="r">순위</th><th>함수명</th><th>언어</th><th>라이브러리</th>` +
      `<th class="s">유사도</th></tr></thead><tbody>${rowsHtml}</tbody></table></body></html>`;

    const w = window.open("", "comantle_top100", "width=480,height=680,scrollbars=yes,resizable=yes");
    if (!w) { setMessage("팝업이 차단됐어요. 브라우저에서 팝업을 허용한 뒤 다시 눌러주세요."); return; }
    w.document.open();
    w.document.write(doc);
    w.document.close();
    w.focus();
  }

  function updateGiveUpBtn() {
    if (!isEnded()) return;
    const btn = $("giveUpBtn");
    if (btn) { btn.disabled = true; btn.classList.add("hidden"); }
  }

  // 다음에 공개할 힌트가 남아 있는지 (언어 -> 라이브러리 순)
  function hintsLeft() {
    return !state.hintLangUsed || !state.hintLibUsed;
  }
  function updateHintBtn() {
    const btn = $("hintBtn");
    if (!btn) return;
    if (!hintsLeft()) {
      btn.disabled = true;
      btn.title = "힌트를 모두 사용했어요";
    }
  }

  // ---- 확인 모달 ----
  let modalYesHandler = null;
  function openModal(text, onYes) {
    $("modalText").textContent = text;
    modalYesHandler = onYes;
    $("modal").classList.remove("hidden");
    $("modalNo").focus();
  }
  function closeModal() {
    $("modal").classList.add("hidden");
    modalYesHandler = null;
  }

  function refreshEndUI() {
    if (!isEnded()) return;
    renderReveal();
    updateGiveUpBtn();
  }

  // ---- 힌트 (정답 언어/라이브러리는 서버 /api/hint 로만 받는다) ----
  function renderHints() {
    const lines = [];
    if (state.hintLangUsed && state.hintLang) {
      lines.push(`<div>🌐 언어: <b>${esc(state.hintLang)}</b></div>`);
    }
    if (state.hintLibUsed && state.hintLib) {
      lines.push(`<div>📚 라이브러리/모듈/클래스: <b>${esc(state.hintLib)}</b></div>`);
    }
    if (lines.length) {
      hintBox.innerHTML = lines.join("");
      hintBox.classList.remove("hidden");
    } else {
      hintBox.classList.add("hidden");
    }
  }

  async function requestHint() {
    if (!hintsLeft()) return;
    const level = state.hintLangUsed ? 2 : 1; // 1 = 언어, 2 = 라이브러리
    let resp;
    try {
      resp = await apiPost("/api/hint", { date: state.date, level });
    } catch (e) {
      setMessage(e.message || "힌트를 받지 못했어요.");
      return;
    }
    if (level === 1) {
      state.hintLangUsed = true;
      state.hintLang = resp.language;
    } else {
      state.hintLibUsed = true;
      state.hintLib = resp.library;
    }
    saveProgress();
    renderHints();
    updateHintBtn();
  }

  // ---- 진행 복원: 종료 상태면 서버에서 정답 공개 정보/top100/힌트 값을 다시 받아 채운다 ----
  // (localStorage 에는 정답·top100·힌트 값을 저장하지 않으므로, 표시에 필요한 값만 재요청.)
  async function restoreFromServerIfNeeded() {
    // 힌트 값 재요청 (플래그만 저장돼 있으므로 값은 서버에서)
    if (state.hintLangUsed && state.hintLang == null) {
      try { state.hintLang = (await apiPost("/api/hint", { date: state.date, level: 1 })).language; } catch (_) {}
    }
    if (state.hintLibUsed && state.hintLib == null) {
      try { state.hintLib = (await apiPost("/api/hint", { date: state.date, level: 2 })).library; } catch (_) {}
    }
    renderHints();
    updateHintBtn();

    if (!isEnded()) return;
    // 종료(맞힘/포기)였으면 정답 공개 정보·top100 을 받아 종료 화면 복원.
    // 서버는 giveup 으로 그날 정답·top100 을 내려준다(이미 종료한 사용자에겐 정당한 재노출).
    try {
      const r = await apiPost("/api/giveup", { date: state.date });
      state.answerId = r.answer.id;
      state.reveal = r.answer;
      state.top100 = r.top100;
    } catch (_) { /* 실패해도 점수/시도 기록은 그대로 보인다 */ }
    renderAttempts();
    refreshEndUI();
  }

  // ---- 한 판(날짜) 시작/재시작 ----
  // 상태와 화면을 초기화하고 그 날짜의 localStorage 기록을 복원한다.
  function resetGameState() {
    state.answerId = null;
    state.attempts = [];
    state.guessed = new Set();
    state.latestId = null;
    state.solved = false;
    state.gaveUp = false;
    state.hintLangUsed = false;
    state.hintLibUsed = false;
    state.hintLang = null;
    state.hintLib = null;
    state.reveal = null;
    state.top100 = null;
  }

  function clearBoardUI() {
    setMessage("");
    input.value = "";
    hideCandidates();
    attemptTable.innerHTML = "";
    attemptCount.textContent = "시도 0회";
    const win = $("winBox");
    win.classList.add("hidden");
    win.classList.remove("gaveup");
    win.innerHTML = "";
    hintBox.classList.add("hidden");
    hintBox.innerHTML = "";
    const gb = $("giveUpBtn");
    if (gb) { gb.disabled = false; gb.classList.remove("hidden"); }
    const hb = $("hintBtn");
    if (hb) { hb.disabled = false; hb.title = "힌트"; }
  }

  // dateOverride 없으면 정상(오늘). 값이 있으면 그 날짜로 시작하되, 서버 DEV_MODE 가 꺼져 있으면
  // 서버가 날짜를 무시하고 오늘을 돌려주므로 새 게임은 자연히 '오늘 다시'가 된다(부정 방지).
  async function startGame(dateOverride) {
    let today;
    try {
      const path = dateOverride
        ? "/api/today?date=" + encodeURIComponent(dateOverride)
        : "/api/today";
      const res = await fetch(apiUrl(path));
      if (!res.ok) throw new Error("today " + res.status);
      today = await res.json();
    } catch (e) {
      setMessage("서버에 연결하지 못했어요. 백엔드(FastAPI)가 떠 있는지 확인해주세요.");
      return false;
    }

    // 함수 목록은 날짜와 무관 → 최초 1회만 구축.
    if (!FUNCTIONS.length) {
      FUNCTIONS = today.functions || [];
      if (!FUNCTIONS.length) { setMessage("함수 목록을 불러오지 못했어요."); return false; }
      buildIndices();
    }

    state.date = today.date;     // 서버가 최종 결정한 날짜(운영이면 항상 오늘)
    state.devMode = !!today.dev;

    resetGameState();
    clearBoardUI();

    // localStorage 진행 복원 (이 날짜 키만)
    const saved = loadRawProgress();
    if (saved) {
      (saved.attempts || []).forEach((a) => {
        if (!byId[a.functionId]) return; // 더 이상 없는 함수면 무시
        state.attempts.push({ id: a.functionId, score: a.score, rank: a.rank });
        state.guessed.add(a.functionId);
        state.latestId = a.functionId;
      });
      state.solved = !!saved.solved;
      state.gaveUp = !!saved.gaveUp;
      state.hintLangUsed = !!saved.hintLangUsed;
      state.hintLibUsed = !!saved.hintLibUsed;
    }
    renderAttempts();
    await restoreFromServerIfNeeded();
    return true;
  }

  // [개발 모드 전용] 유효한 임의 ISO 날짜 — 서버가 hash 로 새 정답을 뽑게 할 '가짜 날짜'.
  // 정답 id 를 직접 지정하지 않는다(노출 금지). 날짜만 바꾼다.
  function randomDevDate() {
    const y = 1000 + Math.floor(Math.random() * 9000);
    const m = 1 + Math.floor(Math.random() * 12);
    const d = 1 + Math.floor(Math.random() * 28); // 1~28 → 모든 달에서 항상 유효
    const pad = (n) => String(n).padStart(2, "0");
    return `${y}-${pad(m)}-${pad(d)}`;
  }

  // ---- 초기화 (이벤트는 1회만 바인딩) ----
  async function init() {
    // 입력 이벤트
    input.addEventListener("input", () => { setMessage(""); hideCandidates(); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); tryTextGuess(input.value); }
      else if (e.key === "Escape") { hideCandidates(); }
    });
    input.addEventListener("blur", () => { setTimeout(hideCandidates, 120); });

    $("enterKey").addEventListener("mousedown", (e) => {
      e.preventDefault();
      tryTextGuess(input.value);
      input.focus();
    });

    // 힌트
    $("hintBtn").addEventListener("click", () => {
      if (!hintsLeft()) return;
      const which = state.hintLangUsed ? "두 번째" : "첫 번째";
      openModal(`${which} 힌트를 보시겠습니까?`, requestHint);
    });

    // 포기
    $("giveUpBtn").addEventListener("click", () => {
      if (isEnded()) return;
      openModal("정말로 포기하시겠습니까?", async () => {
        let r;
        try {
          r = await apiPost("/api/giveup", { date: state.date });
        } catch (e) {
          setMessage(e.message || "포기 처리에 실패했어요.");
          return;
        }
        state.gaveUp = true;
        state.answerId = r.answer.id;
        state.reveal = r.answer;
        state.top100 = r.top100;
        saveProgress();
        renderAttempts();
        refreshEndUI();
      });
    });

    // 모달 버튼/배경/ESC
    $("modalYes").addEventListener("click", () => {
      const h = modalYesHandler;
      closeModal();
      if (h) h();
    });
    $("modalNo").addEventListener("click", closeModal);
    $("modal").addEventListener("click", (e) => {
      if (e.target === $("modal")) closeModal();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !$("modal").classList.contains("hidden")) closeModal();
    });

    // ╔══ 개발 모드 전용 블록 — 출시 시 이 블록만 제거하면 됨 ════════════════════╗
    // 새 게임: 가짜(임의) 날짜를 굴려 서버가 다른 정답을 뽑게 한다. 정답 id 를 직접 지정하지
    // 않는다(노출 금지) — 날짜만 바꾼다. 서버 DEV_MODE 가 꺼져 있으면 서버가 날짜를 무시하므로
    // 무의미 → 버튼도 숨긴다(서버 차단이 최종 방어선, §3 출시 메모).
    $("newGameBtn").addEventListener("click", async () => {
      if (!state.devMode) return; // 운영: 동작하지 않음
      await startGame(randomDevDate());
      input.focus();
    });
    // ╚════════════════════════════════════════════════════════════════════════╝

    const ok = await startGame(); // 오늘 게임 시작 (+ 종료/힌트 복원)
    if (!ok) return;

    // 개발 모드가 아니면 '새 게임' 버튼 숨김 (운영 UI 에서 제거)
    if (!state.devMode) $("newGameBtn").classList.add("hidden");

    input.focus();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
