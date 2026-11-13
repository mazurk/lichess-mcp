"use strict";
const FILES = "abcdefgh";
let state = null, selected = null, pollTimer = null, spectator = false;

const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");
const movesEl = document.getElementById("moves");
const playersEl = document.getElementById("players");

function setStatus(msg, cls) {
  statusEl.textContent = msg || "";
  statusEl.className = cls || "";
}

function setResult(msg) { resultEl.textContent = msg || ""; }

let apiToken = localStorage.getItem("lichessMcpToken") || "";

async function api(path, opts, retried) {
  opts = opts || {};
  opts.headers = Object.assign({}, opts.headers);
  if (apiToken) opts.headers["Authorization"] = "Bearer " + apiToken;
  const res = await fetch(path, opts);
  if (res.status === 401 && !retried) {
    apiToken = prompt("Token d'accès au serveur :", "") || "";
    localStorage.setItem("lichessMcpToken", apiToken);
    return api(path, opts, true);
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || body.error || ("HTTP " + res.status));
  }
  return res.json();
}

document.getElementById("opponentMode").onchange = () => {
  const mode = document.getElementById("opponentMode").value;
  document.getElementById("freeOnly").style.display = mode === "free" ? "block" : "none";
  document.getElementById("engineOnly").style.display = mode === "engine" ? "block" : "none";
};

async function newGame() {
  const yourName = document.getElementById("yourName").value.trim() || "Joueur";
  const yourColor = document.getElementById("color").value;
  const mode = document.getElementById("opponentMode").value;
  const level = document.getElementById("level").value;
  const payload = {
    your_name: yourName,
    your_color: yourColor,
    against_engine: mode === "engine",
    level,
  };
  if (mode === "free") {
    payload.opponent_name = document.getElementById("opponentName").value.trim() || "Adversaire";
  }
  state = await api("/web/api/games", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
  spectator = false;
  document.getElementById("gameId").value = state.game_id;
  afterState();
}

async function joinGame() {
  const game_id = document.getElementById("gameId").value.trim();
  if (!game_id) { setStatus("Entrez un identifiant de partie.", "err"); return; }
  const joinColor = document.getElementById("joinColor").value;
  spectator = joinColor === "spectate";
  const name = document.getElementById("joinName").value.trim() || "Joueur";
  try {
    if (spectator) {
      state = await api("/web/api/games/" + encodeURIComponent(game_id), {method: "GET"});
    } else {
      state = await api("/web/api/games/" + encodeURIComponent(game_id) + "/join", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({name, color: joinColor}),
      });
    }
    if (!spectator) {
      document.getElementById("yourName").value = name;
    }
    afterState();
  } catch (e) { setStatus(e.message, "err"); }
}

function sideName(color) {
  if (!state) return color;
  return color === "white" ? state.white_name : state.black_name;
}

function afterState() {
  selected = null;
  render();
  setResult(state.result || "");
  playersEl.innerHTML = state.white_name === undefined ? "" :
    "Blancs : <b>" + escapeHtml(state.white_name) + "</b><br>Noirs : <b>" + escapeHtml(state.black_name) + "</b>";
  if (state.over) {
    setStatus("Partie terminée.", "ok");
    stopPolling();
  } else {
    if (spectator || state.your_color === null) {
      setStatus("Mode spectateur — au trait : " + sideName(state.turn) + " (" + state.turn + ").");
    } else if (!state.your_turn) {
      setStatus("Au trait : " + sideName(state.turn) + " (" + state.turn + ").");
    } else {
      setStatus("À vous de jouer (" + sideName(state.your_color) + ").");
    }
    startPolling();
  }
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(refresh, 2000);
}
function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

async function refresh() {
  if (!state) return;
  try {
    const prev = JSON.stringify(state);
    const q = spectator || state.your_color === null ? "" : "?your_color=" + state.your_color;
    const next = await api("/web/api/games/" + encodeURIComponent(state.game_id) + q, {method: "GET"});
    if (isStaleResponse(state, next)) return;
    state = next;
    if (JSON.stringify(state) !== prev) afterState();
  } catch (e) { if (String(e.message).includes("404")) { stopPolling(); } }
}

function render() {
  const boardEl = document.getElementById("board");
  boardEl.innerHTML = "";
  if (!state) return;
  const myWhite = state.your_color !== "black";
  const rows = myWhite ? [8,7,6,5,4,3,2,1] : [1,2,3,4,5,6,7,8];
  const cols = myWhite ? FILES.split("") : FILES.split("").reverse();
  const legal = state.legal_moves_uci || [];
  const targetsFrom = selected ? legal.filter(m => m.slice(0,2) === selected) : [];
  for (const r of rows) {
    for (const f of cols) {
      const sq = f + r;
      const div = document.createElement("div");
      const fileIdx = FILES.indexOf(f), rankIdx = r - 1;
      const piece = state.board[7 - rankIdx][fileIdx];
      div.className = "sq " + ((fileIdx + rankIdx) % 2 === 0 ? "light" : "dark");
      if (sq === selected) div.classList.add("sel");
      if (targetsFrom.some(m => m.slice(2,4) === sq)) div.classList.add("target");
      if (state.last_move && (sq === state.last_move.slice(0,2) || sq === state.last_move.slice(2,4))) div.classList.add("last");
      if (piece !== ".") {
        const img = document.createElement("img");
        img.className = "piece";
        img.src = pieceImgSrc(piece);
        img.alt = piece;
        div.appendChild(img);
      }
      if (r === (myWhite ? 1 : 8)) {
        const c = document.createElement("span"); c.className = "coord f"; c.textContent = f; div.appendChild(c);
      }
      if (f === (myWhite ? "h" : "a")) {
        const c = document.createElement("span"); c.className = "coord r"; c.textContent = r; div.appendChild(c);
      }
      div.onclick = () => onSquare(sq);
      boardEl.appendChild(div);
    }
  }
  const analysis = state.analysis;
  const info = (i) => {
    if (!analysis || !analysis.evals || !analysis.evals[i]) return "";
    const e = analysis.evals[i];
    const label = e.label ? " " + e.label : "";
    const best = e.best_move && e.label ? '" title="Meilleur coup : ' + e.best_move + '"' : "";
    return {cls: e.label || "", title: best, label};
  };
  movesEl.innerHTML = state.moves.length
    ? state.moves.map((m, i) => {
        if (i % 2 === 0) {
          const blackMove = state.moves[i + 1];
          const wi = info(i), bi = info(i + 1);
          return '<span class="num">' + (i/2+1) + '.</span>'
            + '<span class="mv-w ' + wi.cls + '"' + (wi.title || "") + '>' + m + '</span>'
            + '<span class="mv-b ' + bi.cls + '"' + (bi.title || "") + '>' + (blackMove || "") + '</span>';
        }
        return "";
      }).join("")
    : "<i>Aucun coup joué.</i>";
  renderEvalChart();
  renderBoardLabels();
}

function evalWhiteToPct(evalWhite) {
  // Conversion centipions (perspective blancs) en hauteur de barre (0-100 %).
  // Les mats sont ramenés aux bornes.
  const cp = Math.max(-1500, Math.min(1500, evalWhite));
  return 50 + (cp / 1500) * 50;
}

function evalWhiteToText(evalWhite) {
  if (evalWhite >= 90000) return "M" + Math.max(1, Math.round((100000 - evalWhite) / 100));
  if (evalWhite <= -90000) return "-M" + Math.max(1, Math.round((100000 + evalWhite) / 100));
  return (evalWhite / 100).toFixed(1);
}

function renderEvalChart() {
  const chart = document.getElementById("evalchart");
  const legend = document.getElementById("evallegend");
  const analysis = state && state.analysis;
  if (!analysis || !analysis.evals || !analysis.evals.length) {
    chart.style.display = "none";
    chart.innerHTML = "";
    legend.style.display = "none";
    return;
  }
  chart.style.display = "flex";
  legend.style.display = "block";
  chart.innerHTML = "";
  for (const e of analysis.evals) {
    const bar = document.createElement("div");
    bar.className = "evalbar" + (e.label === "blunder" ? " crit" : "");
    const pct = evalWhiteToPct(e.eval_white);
    const whitePart = document.createElement("div");
    whitePart.className = "white-adv";
    whitePart.style.height = pct + "%";
    const blackPart = document.createElement("div");
    blackPart.className = "black-adv";
    blackPart.style.height = (100 - pct) + "%";
    bar.appendChild(whitePart);
    bar.appendChild(blackPart);
    bar.title = "Coup " + e.move_number + " " + e.san
      + (e.label ? " (« " + e.label + " »)" : "")
      + " — éval. " + evalWhiteToText(e.eval_white);
    chart.appendChild(bar);
  }
  const last = analysis.evals[analysis.evals.length - 1];
  legend.innerHTML = "Analyse Stockfish (profondeur " + analysis.depth + ") — "
    + "dernière éval. : <b>" + evalWhiteToText(last.eval_white) + "</b> — "
    + "<span class='blunder'>■</span> blunder <span class='mistake'>■</span> erreur <span class='inaccuracy'>■</span> imprécision";
}

function renderBoardLabels() {
  const top = document.getElementById("labelTop");
  const bottom = document.getElementById("labelBottom");
  if (!state) { top.textContent = ""; bottom.textContent = ""; return; }
  const myWhite = state.your_color !== "black";
  const topColor = myWhite ? "black" : "white";
  const bottomColor = myWhite ? "white" : "black";
  const label = (color) => {
    const name = color === "white" ? state.white_name : state.black_name;
    const seat = color === "white" ? state.white_seat : state.black_seat;
    const turnMark = state.turn === color && !state.over ? ' <span class="turn-ind">•</span>' : "";
    const seatInfo = seat ? '<span class="seat">occupé par ' + escapeHtml(seat) + '</span>'
                          : '<span class="seat">camp libre</span>';
    return (color === "white" ? "Blancs" : "Noirs") + " : <b>" + escapeHtml(name) + "</b>" + turnMark + " " + seatInfo;
  };
  top.innerHTML = label(topColor);
  bottom.innerHTML = label(bottomColor);
}

function onSquare(sq) {
  if (!state || state.over || spectator || state.your_color === null) return;
  if (!state.your_turn) return;
  const legal = state.legal_moves_uci || [];
  if (selected) {
    const candidates = legal.filter(m => m.slice(0,2) === selected && m.slice(2,4) === sq);
    if (candidates.length) {
      let move = candidates[0];
      if (candidates.length > 1) {
        const promo = prompt("Promotion : q (dame), r (tour), b (fou), n (cavalier) ?", "q");
        move = candidates.find(m => m.length === 5 && m[4] === (promo || "q")) || move;
      }
      playMove(move);
      return;
    }
  }
  const hasMoves = legal.some(m => m.slice(0,2) === sq);
  selected = hasMoves ? sq : null;
  render();
}

async function playMove(uci) {
  setStatus("Envoi du coup…");
  stopPolling();
  try {
    state = await api("/web/api/games/" + encodeURIComponent(state.game_id) + "/move", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        move: uci,
        your_color: state.your_color,
        your_name: document.getElementById("yourName").value.trim() || "Joueur",
      }),
    });
    afterState();
  } catch (e) {
    setStatus(e.message, "err");
    selected = null;
    render();
    startPolling();
  }
}

document.getElementById("btnNew").onclick = () => newGame().catch(e => setStatus(e.message, "err"));
document.getElementById("btnJoin").onclick = joinGame;
