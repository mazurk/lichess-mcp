"use strict";
const FILES = "abcdefgh";
let state = null, selected = null, pollTimer = null, spectator = false;
// Navigation dans l'historique : null = position en direct (dernier coup),
// sinon index du coup affiché (0 = position après le 1er coup).
let viewPly = null;
// Mode du cadre « Choix de la partie » : create / join / analysis / dryrun
let mode = "create";

const MODE_HINTS = {
  create: "Démarrez une nouvelle partie : contre le moteur, un agent IA ou un humain.",
  join: "Rejoignez une partie en cours ou terminée (non analysée) : joueur ou spectateur.",
  analysis: "Ouvrez une partie terminée et analysée pour le debrief (consultation).",
  dryrun: "Consultez un échiquier d'analyse (dry-run) créé par l'agent.",
};
const MODE_LABELS = {
  join: "Rejoindre",
  analysis: "Analyser",
  dryrun: "Consulter",
};

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
  // la liste sera rafraîchie par refreshGameList() (le champ est un <select>)
  afterState();
}

async function joinGame() {
  const game_id = document.getElementById("gameId").value;
  if (!game_id) { setStatus("Créez une partie ou sélectionnez-en une dans la liste.", "err"); return; }
  // Les modes Analyse et Dry-run sont en consultation : spectateur imposé.
  const joinColor = (mode === "analysis" || mode === "dryrun")
    ? "spectate" : document.getElementById("joinColor").value;
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

function isLive() {
  // En direct si aucune navigation, ou position du dernier coup atteinte.
  if (viewPly === null) return true;
  const hist = state && state.history ? state.history : [];
  return viewPly >= hist.length - 1;
}

function render() {
  const boardEl = document.getElementById("board");
  boardEl.innerHTML = "";
  if (!state) return;
  const myWhite = state.your_color !== "black";
  const rows = myWhite ? [8,7,6,5,4,3,2,1] : [1,2,3,4,5,6,7,8];
  const cols = myWhite ? FILES.split("") : FILES.split("").reverse();
  // Position affichée : historique navigué ou position en direct.
  const hist = state.history || [];
  let grid, lastMove, legal;
  if (viewPly !== null && viewPly < hist.length - 1) {
    grid = fenToGrid(hist[viewPly].fen);
    lastMove = hist[viewPly].uci;
    legal = [];
  } else {
    grid = state.board;
    lastMove = state.last_move;
    legal = state.legal_moves_uci || [];
  }
  const targetsFrom = selected ? legal.filter(m => m.slice(0,2) === selected) : [];
  for (const r of rows) {
    for (const f of cols) {
      const sq = f + r;
      const div = document.createElement("div");
      const fileIdx = FILES.indexOf(f), rankIdx = r - 1;
      const piece = grid[7 - rankIdx][fileIdx];
      div.className = "sq " + ((fileIdx + rankIdx) % 2 === 0 ? "light" : "dark");
      if (sq === selected) div.classList.add("sel");
      if (targetsFrom.some(m => m.slice(2,4) === sq)) div.classList.add("target");
      if (lastMove && (sq === lastMove.slice(0,2) || sq === lastMove.slice(2,4))) div.classList.add("last");
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
  renderMoves();
  renderEvalChart();
  renderBoardLabels();
  renderGameStatus();
}

function moveInfo(i) {
  // Étiquette d'analyse + info-bulle (éval, meilleur coup, PV) pour le coup i.
  const analysis = state && state.analysis;
  if (!analysis || !analysis.evals || !analysis.evals[i]) return {cls: "", title: "", evalv: ""};
  const e = analysis.evals[i];
  let title = "Coup " + e.move_number + " " + e.san + " — éval. " + evalWhiteToText(e.eval_white);
  if (e.label) title += " (« " + e.label + " »)";
  if (e.pv_san && e.pv_san.length) {
    title += "\nVariante Stockfish : " + e.pv_san.join(" ");
  } else if (e.best_move) {
    title += "\nMeilleur coup : " + e.best_move;
  }
  return {
    cls: e.label || "",
    title: ' title="' + escapeHtml(title) + '"',
    evalv: ' <span class="evalv">' + evalWhiteToText(e.eval_white) + "</span>",
  };
}

function renderMoves() {
  if (!state.moves.length) {
    movesEl.innerHTML = "<i>Aucun coup joué.</i>";
    return;
  }
  const current = (viewPly === null || isLive()) ? state.moves.length - 1 : viewPly;
  const moveHtml = (i) => {
    const m = state.moves[i];
    const inf = moveInfo(i);
    return '<span class="mv' + (i === current ? " cur" : "") + " " + inf.cls
      + '" data-ply="' + i + '"' + inf.title + '>'
      + m + inf.evalv + "</span>";
  };
  let html = "";
  for (let i = 0; i < state.moves.length; i += 2) {
    html += '<span class="num">' + (i/2+1) + '.</span>'
      + '<span class="mvcell">' + moveHtml(i) + "</span>"
      + '<span class="mvcell">' + (i + 1 < state.moves.length ? moveHtml(i + 1) : "") + "</span>";
  }
  movesEl.innerHTML = html;
  movesEl.querySelectorAll(".mv").forEach((el) => {
    el.onclick = () => setView(Number(el.dataset.ply));
  });
}

function setView(ply) {
  // Navigation dans l'historique ; le dernier coup revient en direct.
  const hist = state.history || [];
  if (ply === null || ply >= hist.length - 1) {
    viewPly = null;
  } else {
    viewPly = Math.max(0, Math.min(ply, hist.length - 1));
  }
  selected = null;
  render();
}

function navFirst() { if (state && state.history && state.history.length) setView(0); }
function navPrev() { if (state && state.history && state.history.length) setView((viewPly === null ? state.history.length - 1 : viewPly) - 1); }
function navNext() { if (state && state.history && state.history.length) setView(viewPly === null ? null : viewPly + 1); }
function navLast() { setView(null); }

document.addEventListener("keydown", (ev) => {
  if (!state || ev.target.tagName === "INPUT" || ev.target.tagName === "SELECT") return;
  if (ev.key === "ArrowLeft") { navPrev(); ev.preventDefault(); }
  else if (ev.key === "ArrowRight") { navNext(); ev.preventDefault(); }
});

function renderGameStatus() {
  const el = document.getElementById("gameStatus");
  if (!state) { el.innerHTML = ""; return; }
  const badge = state.over ? "terminée" : "en cours";
  const cls = state.over ? "over" : "live";
  let html = '<span class="badge ' + cls + '">' + (state.over ? "" : "● ") + badge + "</span>";
  if (state.analysed && state.analysis) {
    html += ' <span class="badge analysed">analysée (prof. ' + state.analysis.depth + ")</span>";
  }
  if (viewPly !== null && !isLive()) {
    html += ' <span class="badge browsing">relecture : coup '
      + (viewPly + 1) + "/" + state.history.length
      + ' — <a href="#" id="backLive">retour au direct</a></span>';
  }
  el.innerHTML = html;
  const back = document.getElementById("backLive");
  if (back) back.onclick = (e) => { e.preventDefault(); navLast(); };
}

function evalWhiteToPct(evalWhite) {
  // Conversion centipions (perspective blancs) en hauteur de barre (0-100 %).
  // Les mats sont ramenés aux bornes.
  const cp = Math.max(-1500, Math.min(1500, evalWhite));
  return 50 + (cp / 1500) * 50;
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
  // Libellé compact et unique par camp : « Blancs — libre » ou « Blancs — Nicolas ».
  // Priorité d'affichage : occupant > moteur > nom > libre.
  const label = (color) => {
    const name = color === "white" ? state.white_name : state.black_name;
    const seat = color === "white" ? state.white_seat : state.black_seat;
    let who;
    if (seat) {
      who = escapeHtml(seat);
    } else if (state.engine_color === color) {
      who = "Moteur";
    } else if (name && name !== (color === "white" ? "Blancs" : "Noirs")) {
      who = escapeHtml(name);
    } else {
      who = "libre";
    }
    const isTurn = state.turn === color && !state.over;
    const turnMark = isTurn ? ' <span class="turn-ind" title="camp au trait">●</span>' : "";
    return '<span class="' + (isTurn ? "to-move" : "") + '">'
      + (color === "white" ? "Blancs" : "Noirs") + " — " + who + turnMark + "</span>";
  };
  top.innerHTML = label(topColor);
  bottom.innerHTML = label(bottomColor);
}

function onSquare(sq) {
  if (!state || state.over || spectator || state.your_color === null) return;
  if (!state.your_turn) return;
  if (!isLive()) { setStatus("Relecture : revenez au dernier coup pour jouer.", "err"); return; }
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

document.getElementById("btnNew").onclick = () => newGame().then(refreshGameList).catch(e => setStatus(e.message, "err"));
document.getElementById("btnJoin").onclick = joinGame;
document.getElementById("btnFirst").onclick = navFirst;
document.getElementById("btnPrev").onclick = navPrev;
document.getElementById("btnNext").onclick = navNext;
document.getElementById("btnLast").onclick = navLast;
document.getElementById("btnRefreshGames").onclick = () => refreshGameList().catch(e => setStatus(e.message, "err"));

function updateMode() {
  mode = document.getElementById("modeSelect").value;
  // section Créer visible seulement en mode create
  document.getElementById("mode-create").style.display = (mode === "create") ? "block" : "none";
  // la section join héberge aussi les modes analysis/dryrun (liste + consultation)
  document.getElementById("mode-join").style.display = (mode === "create") ? "none" : "block";
  // le choix de couleur (joueur/spectateur) n'a de sens qu'en mode Rejoindre
  document.getElementById("joinColorWrap").style.display = (mode === "join") ? "block" : "none";
  document.getElementById("btnJoin").textContent = MODE_LABELS[mode] || "Rejoindre";
  document.getElementById("modeHint").textContent = MODE_HINTS[mode] || "";
  refreshGameList().catch(() => {});
}

function filterGamesForMode(games) {
  // Filtrage selon le mode : le sélecteur ne propose que les parties
  // pertinentes (séparation parties classiques / échiquiers d'analyse).
  if (mode === "join") {
    return games.filter(g => g.kind === "game" && !g.analysed);
  }
  if (mode === "analysis") {
    return games.filter(g => g.kind === "game" && g.analysed && g.status === "terminée");
  }
  if (mode === "dryrun") {
    return games.filter(g => g.kind === "analysis");
  }
  return games;
}

async function refreshGameList() {
  // Liste déroulante des parties du serveur, filtrée selon le mode.
  const data = await api("/web/api/games", {method: "GET"});
  const select = document.getElementById("gameId");
  const previous = select.value;
  select.innerHTML = "";
  const games = filterGamesForMode(data.games || []);
  if (!games.length) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = mode === "dryrun" ? "Aucun échiquier d'analyse sur le serveur"
      : mode === "analysis" ? "Aucune partie analysée à consulter"
      : "Aucune partie sur le serveur";
    select.appendChild(opt);
    return;
  }
  for (const g of games) {
    const opt = document.createElement("option");
    opt.value = g.game_id;
    const label = g.game_id + " — " + g.white_name + " vs " + g.black_name
      + " · " + g.plies + " coups · " + g.status
      + (g.analysed ? " · analysée" : "");
    opt.textContent = label;
    select.appendChild(opt);
  }
  if (previous && games.some(g => g.game_id === previous)) {
    select.value = previous;
  }
}
document.getElementById("modeSelect").onchange = updateMode;
refreshGameList().catch(() => {});
updateMode();
