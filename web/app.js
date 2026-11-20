"use strict";
const FILES = "abcdefgh";
let state = null, selected = null, pollTimer = null, spectator = false;
// Navigation dans l'historique : null = position en direct (dernier coup),
// sinon index du coup affiché (0 = position après le 1er coup).
let viewPly = null;
// Mode du cadre « Choix de la partie » : create / join / analysis / dryrun / puzzle
let mode = "create";

const MODE_HINTS = {
  create: "Démarrez une nouvelle partie : contre le moteur, un agent IA ou un humain.",
  join: "Rejoignez une partie en cours ou terminée (non analysée) : joueur ou spectateur.",
  analysis: "Ouvrez une partie terminée et analysée pour le debrief (consultation).",
  dryrun: "Consultez un échiquier d'analyse (dry-run) créé par l'agent (spectateur).",
  puzzle: "Consultez les puzzles publiés par l'agent (mode puzzle) : position, coups joués et progression (spectateur).",
};
const MODE_LABELS = {
  join: "Rejoindre",
  analysis: "Analyser",
  dryrun: "Consulter",
  puzzle: "Consulter",
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
    clock: document.getElementById("clockMode").value,
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
  try {
    if (mode === "analysis") {
      // Consultation d'une partie analysée : pas de nom, perspective au choix.
      const viewColor = document.getElementById("viewColor").value;
      spectator = true;
      state = await api("/web/api/games/" + encodeURIComponent(game_id) + "?your_color=" + viewColor,
        {method: "GET"});
    } else if (mode === "dryrun" || mode === "puzzle") {
      // Consultation d'un échiquier d'analyse : spectateur uniquement.
      spectator = true;
      state = await api("/web/api/games/" + encodeURIComponent(game_id), {method: "GET"});
    } else {
      const joinColor = document.getElementById("joinColor").value;
      spectator = joinColor === "spectate";
      const name = document.getElementById("joinName").value.trim() || "Joueur";
      if (spectator) {
        // Le spectateur s'enregistre : il compte dans le compteur de la partie.
        state = await api("/web/api/games/" + encodeURIComponent(game_id) + "/join", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({name, spectate: true}),
        });
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
    }
    afterState();
  } catch (e) { setStatus(e.message, "err"); }
}

function sideName(color) {
  if (!state) return color;
  return color === "white" ? state.white_name : state.black_name;
}

let lastStateKey = "";

function afterState() {
  stateFetchedAt = Date.now();
  // Ne réinitialiser la sélection QUE si la partie a réellement évolué :
  // le poll (2 s) re-rend l'affichage (l'horloge change à chaque fois),
  // il ne doit pas effacer la pièce sélectionnée en cours de coup.
  const key = state.game_id + ":" + state.moves.length + ":"
    + (state.over ? "1" : "0") + ":" + (state.your_turn ? "1" : "0");
  if (key !== lastStateKey) selected = null;
  lastStateKey = key;
  render();
  setResult(state.result || "");
  playersEl.innerHTML = state.white_name === undefined ? "" :
    "Blancs : <b>" + escapeHtml(state.white_name) + "</b><br>Noirs : <b>" + escapeHtml(state.black_name) + "</b>";
  // Bouton d'abandon : visible uniquement pour un joueur (pas un spectateur),
  // tant que la partie est en cours.
  const btnResign = document.getElementById("btnResign");
  if (btnResign) {
    btnResign.style.display =
      (!state.over && !spectator && state.your_color !== null && state.your_color !== undefined)
        ? "block" : "none";
  }
  // Alerte « triche détectée » sous l'échiquier : tout analyze_game appelé
  // pendant qu'une partie ou un puzzle est en cours (même sur un autre
  // échiquier) est remonté ici.
  const cheatEl = document.getElementById("cheatAlert");
  if (cheatEl) {
    if (state.cheat_detected) {
      cheatEl.style.display = "block";
      cheatEl.textContent = "Triche détectée";
    } else {
      cheatEl.style.display = "none";
    }
  }
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
  // viewPly = nombre de coups joués dans la vue (0 = position de départ)
  return viewPly >= hist.length;
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
  if (viewPly !== null && viewPly < hist.length) {
    // 0 = position de départ (FEN racine, avant le premier coup)
    grid = viewPly === 0 ? fenToGrid(state.start_fen)
      : fenToGrid(hist[viewPly - 1].fen);
    lastMove = viewPly > 0 ? hist[viewPly - 1].uci : null;
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
  const current = (viewPly === null || isLive()) ? state.moves.length - 1 : viewPly - 1;
  const moveHtml = (i) => {
    const m = state.moves[i];
    const inf = moveInfo(i);
    // temps consommé sur ce coup (horloges) : indicateur de debrief
    let timev = "";
    const h = state.history && state.history[i];
    if (h && h.clock) {
      const s_ = h.clock.spent_ms / 1000;
      timev = ' <span class="timev">' + (s_ >= 10 ? Math.round(s_) + "s" : s_.toFixed(1) + "s") + "</span>";
    }
    return '<span class="mv' + (i === current ? " cur" : "") + " " + inf.cls
      + '" data-ply="' + i + '"' + inf.title + '>'
      + m + inf.evalv + timev + "</span>";
  };
  let html = "";
  for (let i = 0; i < state.moves.length; i += 2) {
    html += '<span class="num">' + (i/2+1) + '.</span>'
      + '<span class="mvcell">' + moveHtml(i) + "</span>"
      + '<span class="mvcell">' + (i + 1 < state.moves.length ? moveHtml(i + 1) : "") + "</span>";
  }
  movesEl.innerHTML = html;
  movesEl.querySelectorAll(".mv").forEach((el) => {
    el.onclick = () => setView(Number(el.dataset.ply) + 1);
  });
}

function setView(ply) {
  // Navigation dans l'historique ; ply = nombre de coups joués dans la vue
  // (0 = position de départ) ; le dernier coup revient en direct.
  const hist = state.history || [];
  if (ply === null || ply >= hist.length) {
    viewPly = null;
  } else {
    viewPly = Math.max(0, Math.min(ply, hist.length));
  }
  selected = null;
  render();
}

function navFirst() { if (state && state.history && state.history.length) setView(0); }
function navPrev() { if (state && state.history && state.history.length) setView((viewPly === null ? state.history.length : viewPly) - 1); }
function navNext() { if (state && state.history && state.history.length) setView(viewPly === null ? null : viewPly + 1); }
function navLast() { setView(null); }

document.addEventListener("keydown", (ev) => {
  if (!state || ev.target.tagName === "INPUT" || ev.target.tagName === "SELECT") return;
  if (ev.key === "ArrowLeft") { navPrev(); ev.preventDefault(); }
  else if (ev.key === "ArrowRight") { navNext(); ev.preventDefault(); }
});

let stateFetchedAt = 0;

function clockFormat(ms) {
  ms = Math.max(0, ms);
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return m + ":" + (sec < 10 ? "0" : "") + sec;
}

function clockText(color) {
  // Temps restant affiché : valeur serveur (au dernier poll) moins le temps
  // écoulé si c'est le camp au trait — le poll (2 s) resynchronise.
  if (!state || !state.clock) return "";
  let ms = color === "white" ? state.clock.white_ms : state.clock.black_ms;
  if (state.clock.running === color && !state.over) {
    ms -= Date.now() - stateFetchedAt;
  }
  return clockFormat(ms);
}

function browsingClockMs(color) {
  // En relecture : temps restant d'un camp au pli consulté = son dernier
  // relevé d'horloge parmi les coups 0..viewPly (sinon le temps de base).
  const hist = state.history || [];
  const upto = (viewPly === null) ? hist.length : viewPly;
  for (let i = upto - 1; i >= 0; i--) {
    const c = hist[i] && hist[i].clock;
    if (c && c.color === color) return c.remaining_ms;
  }
  return (typeof state.clock.base_ms === "number") ? state.clock.base_ms : null;
}

function updateClocks() {
  if (!state || !state.clock) return;
  const browsing = viewPly !== null && !isLive();
  for (const color of ["white", "black"]) {
    const el = document.getElementById("clock-" + color);
    if (!el) continue;
    if (browsing) {
      const ms = browsingClockMs(color);
      el.textContent = (ms === null) ? "" : clockFormat(ms);
      el.className = "clock" + ((ms !== null && ms < 30000) ? " low" : "");
    } else {
      el.textContent = clockText(color);
      el.className = "clock"
        + (state.clock.running === color && !state.over ? " running" : "")
        + ((color === "white" ? state.clock.white_ms : state.clock.black_ms) < 30000 ? " low" : "");
    }
  }
}
setInterval(updateClocks, 500);

function renderGameStatus() {
  const el = document.getElementById("gameStatus");
  if (!state) { el.innerHTML = ""; return; }
  const badge = state.over ? "terminée" : "en cours";
  const cls = state.over ? "over" : "live";
  let html = '<span class="badge ' + cls + '">' + (state.over ? "" : "● ") + badge + "</span>";
  if (state.analysis_running) {
    html += ' <span class="badge live">● analyse en cours…</span>';
  } else if (state.analysis_queued) {
    html += ' <span class="badge queued">⏳ analyse en file d\'attente…</span>';
  }
  if (state.analysed && state.analysis) {
    html += ' <span class="badge analysed">analysée (prof. ' + state.analysis.depth + ")</span>";
  }
  if (state.puzzle) {
    html += ' <span class="badge puzzle">puzzle ' + escapeHtml(state.puzzle.puzzle_id)
      + " · " + state.puzzle.plies_done + "/" + state.puzzle.total_plies
      + (state.puzzle.solved ? " · résolu ✓" : "") + "</span>";
  }
  if (state.spectators > 0) {
    html += ' <span class="badge spectators">👁 ' + state.spectators
      + (state.spectators > 1 ? " spectateurs" : " spectateur") + "</span>";
  }
  if (viewPly !== null && !isLive()) {
    html += ' <span class="badge browsing">relecture : coup '
      + viewPly + "/" + state.history.length
      + ' — <a href="#" id="backLive">retour au direct</a></span>';
  }
  el.innerHTML = html;
  const back = document.getElementById("backLive");
  if (back) back.onclick = (e) => { e.preventDefault(); navLast(); };
}

function renderEvalChart() {
  // Courbe d'avantage façon Lichess : SVG, avantage blancs vers le haut,
  // marqueurs colorés sur les coups gaffés, survol = coup + évaluation.
  const chart = document.getElementById("evalchart");
  const legend = document.getElementById("evallegend");
  const analysis = state && state.analysis;
  if (!analysis || !analysis.evals || !analysis.evals.length) {
    chart.style.display = "none";
    chart.innerHTML = "";
    legend.style.display = "none";
    return;
  }
  const evals = analysis.evals;
  chart.style.display = "block";
  legend.style.display = "block";

  const W = 320, H = 80, PAD = 4;
  const mid = H / 2;
  const maxAbs = 800; // ±8 pions, au-delà la courbe est écrasée aux bornes
  const x = (i) => (evals.length === 1 ? W / 2 : PAD + (i / (evals.length - 1)) * (W - 2 * PAD));
  const y = (v) => {
    const cl = Math.max(-maxAbs, Math.min(maxAbs, v));
    return mid - (cl / maxAbs) * (mid - PAD);
  };
  const pts = evals.map((e, i) => x(i).toFixed(1) + "," + y(e.eval_white).toFixed(1));
  const area = "M" + PAD + "," + mid
    + " L" + pts.join(" L") + " L" + (W - PAD).toFixed(1) + "," + mid + " Z";

  const LABEL_COLORS = { blunder: "#d34f5a", mistake: "#e8a33d", inaccuracy: "#d7c14a" };
  let dots = "";
  let hovers = "";
  for (let i = 0; i < evals.length; i++) {
    const e = evals[i];
    const cx = x(i).toFixed(1), cy = y(e.eval_white).toFixed(1);
    const tip = "Coup " + e.move_number + " " + e.san
      + (e.label ? " (« " + e.label + " »)" : "")
      + " — éval. " + evalWhiteToText(e.eval_white);
    if (e.label) {
      dots += '<circle class="ev-dot" cx="' + cx + '" cy="' + cy
        + '" r="3.2" fill="' + (LABEL_COLORS[e.label] || "#fff") + '" stroke="#262421" stroke-width="0.8"/>';
    }
    hovers += '<circle cx="' + cx + '" cy="' + cy + '" r="9" fill="transparent">'
      + "<title>" + escapeHtml(tip) + "</title></circle>";
  }

  chart.innerHTML = '<svg viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none" role="img" '
    + 'aria-label="Courbe d\'avantage de la partie">'
    + '<line x1="' + PAD + '" y1="' + mid + '" x2="' + (W - PAD) + '" y2="' + mid
    + '" stroke="#4d4b47" stroke-width="0.6" stroke-dasharray="2,3"/>'
    + '<path d="' + area + '" fill="rgba(140,166,76,0.18)"/>'
    + '<polyline points="' + pts.join(" ") + '" fill="none" stroke="#8ca64c" stroke-width="1.6"'
    + ' stroke-linejoin="round" stroke-linecap="round"/>'
    + dots + hovers + "</svg>";

  const last = evals[evals.length - 1];
  legend.innerHTML = "Analyse Stockfish (profondeur " + analysis.depth + ") — "
    + "dernière éval. : <b>" + evalWhiteToText(last.eval_white) + "</b> — "
    + "<span class='blunder'>●</span> blunder <span class='mistake'>●</span> erreur <span class='inaccuracy'>●</span> imprécision";
}

function renderBoardLabels() {
  const top = document.getElementById("labelTop");
  const bottom = document.getElementById("labelBottom");
  if (!state) { top.textContent = ""; bottom.textContent = ""; return; }
  const myWhite = state.your_color !== "black";
  const topColor = myWhite ? "black" : "white";
  const bottomColor = myWhite ? "white" : "black";
  // Libellé compact et unique par camp : « Blancs — libre » ou « Blancs — Alice ».
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
  top.innerHTML = label(topColor)
    + '<span class="clock" id="clock-' + topColor + '"></span>';
  bottom.innerHTML = label(bottomColor)
    + '<span class="clock" id="clock-' + bottomColor + '"></span>';
  updateClocks();
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

async function resignGame() {
  // Abandon du camp du joueur connecté (jamais pour un spectateur : le bouton
  // est masqué, et le serveur rejette de toute façon un your_color invalide).
  if (!state || spectator || !state.your_color || state.over) return;
  if (!confirm("Abandonner la partie ? Votre adversaire gagne.")) return;
  stopPolling();
  try {
    state = await api("/web/api/games/" + encodeURIComponent(state.game_id) + "/resign", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        your_color: state.your_color,
        your_name: document.getElementById("yourName").value.trim() || "Joueur",
      }),
    });
    afterState();
  } catch (e) {
    setStatus(e.message, "err");
    startPolling();
  }
}

document.getElementById("btnNew").onclick = () => newGame().then(refreshGameList).catch(e => setStatus(e.message, "err"));
document.getElementById("btnJoin").onclick = joinGame;
document.getElementById("btnResign").onclick = resignGame;
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
  // le nom ne sert qu'à rejoindre en tant que joueur ; Analyse/Dry-run = consultation
  document.getElementById("joinNameWrap").style.display = (mode === "join") ? "block" : "none";
  // en mode Analyse, on choisit la perspective d'affichage à la place du nom
  document.getElementById("viewColorWrap").style.display = (mode === "analysis") ? "block" : "none";
  document.getElementById("btnJoin").textContent = MODE_LABELS[mode] || "Rejoindre";
  document.getElementById("modeHint").textContent = MODE_HINTS[mode] || "";
  // panneau des scores par thème, uniquement en mode Puzzles (issue #72)
  document.getElementById("puzzleStats").style.display = (mode === "puzzle") ? "block" : "none";
  if (mode === "puzzle") { loadPuzzleStats().catch(() => {}); }
  refreshGameList().catch(() => {});
}

// Scores d'entraînement par thème de puzzle (issue #72) : taux de coups
// validés et de résolutions au premier essai, pour cibler l'entraînement.
async function loadPuzzleStats() {
  const el = document.getElementById("puzzleStats");
  const data = await api("/web/api/puzzle_stats", {method: "GET"});
  const t = data.total || {};
  let html = '<b>Scores par thème</b> — ' + (t.solved || 0) + ' résolu(s), '
    + (t.correct || 0) + '/' + (t.attempts || 0) + ' coups OK (' + (t.success_rate || 0) + ' %)'
    + (t.solved ? ', 1er essai ' + (t.first_try_rate || 0) + ' %' : '') + '<br>';
  const themes = Object.keys(data.by_theme || {});
  if (!themes.length) {
    html += '<i>Aucun essai enregistré pour le moment.</i>';
  } else {
    html += themes.map(function (th) {
      const s = data.by_theme[th];
      return escapeHtml(th) + ' : ' + s.solved + ' résolu(s) · ' + s.correct + '/' + s.attempts
        + ' (' + s.success_rate + ' %)'
        + (s.solved ? ' · 1er essai ' + s.first_try_rate + ' %' : '');
    }).join('<br>');
  }
  el.innerHTML = html;
}

function filterGamesForMode(games) {
  // Filtrage selon le mode : le sélecteur ne propose que les parties
  // pertinentes (séparation parties classiques / échiquiers d'analyse).
  if (mode === "join") {
    // Parties classiques joignables : non analysées, plus les parties EN COURS
    // déjà analysées par l'agent (affichées en rouge dans la liste).
    return games.filter(g => g.kind === "game" && (!g.analysed || g.status === "en cours"));
  }
  if (mode === "analysis") {
    return games.filter(g => g.kind === "game" && g.analysed && g.status === "terminée");
  }
  if (mode === "dryrun") {
    return games.filter(g => g.kind === "analysis");
  }
  if (mode === "puzzle") {
    // puzzles publiés par l'agent (consultation/spectateur)
    return games.filter(g => g.kind === "puzzle");
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
      : mode === "puzzle" ? "Aucun puzzle publié sur le serveur"
      : "Aucune partie sur le serveur";
    select.appendChild(opt);
    return;
  }
  for (const g of games) {
    const opt = document.createElement("option");
    opt.value = g.game_id;
    const ongoingAnalysed = g.analysed && g.status === "en cours";
    const who = (g.kind === "analysis" || g.kind === "puzzle") && g.name
      ? g.name : g.white_name + " vs " + g.black_name;
    let label = g.game_id + " — " + who
      + " · " + g.plies + " coups · " + g.status
      + (g.analysis_running ? " · analyse en cours…"
        : g.analysis_queued ? " · analyse en file…" : "")
      + (g.analysed ? (ongoingAnalysed ? " · ANALYSÉE en cours !" : " · analysée") : "");
    if (g.puzzle) {
      label += " · puzzle " + g.puzzle.puzzle_id
        + " " + g.puzzle.plies_done + "/" + g.puzzle.total_plies
        + (g.puzzle.solved ? " ✓" : "");
    }
    opt.textContent = label;
    if (ongoingAnalysed) opt.style.color = "#d34f5a";
    select.appendChild(opt);
  }
  if (previous && games.some(g => g.game_id === previous)) {
    select.value = previous;
  }
}
// Badge de version en coin haut gauche (issue #67) : permet de valider
// rapidement quelle version est déployée sur le serveur (serveur de production).
async function loadVersionBadge() {
  try {
    const v = await api("/web/api/version");
    const el = document.getElementById("versionBadge");
    el.textContent = "v" + v.version + (v.commit ? " (" + v.commit + ")" : "");
    el.title = "Version déployée : v" + v.version + " — commit " + (v.commit || "inconnu");
  } catch (e) { /* badge discret : ne jamais bloquer l'UI */ }
}

document.getElementById("modeSelect").onchange = updateMode;
refreshGameList().catch(() => {});
updateMode();
loadVersionBadge();
