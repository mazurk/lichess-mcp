// Fonctions pures de la page web — testées par tests/web/util.test.mjs (node --test).

function isStaleResponse(currentState, fetchedState) {
  // Réponse périmée (course poll/coup) : autre partie, ou moins de coups
  // que l'état déjà affiché — on doit l'ignorer pour ne pas écraser
  // l'affichage d'une position plus récente.
  if (!fetchedState || typeof fetchedState !== "object") return true;
  if (!currentState || currentState.game_id !== fetchedState.game_id) return true;
  if (!Array.isArray(fetchedState.moves)) return true;
  if (Array.isArray(currentState.moves) && fetchedState.moves.length < currentState.moves.length) {
    return true;
  }
  return false;
}

function pieceGlyph(p) {
  // Pièces blanches : glyphes pleins (♚…) remplis de blanc net.
  // Pièces noires : glyphes contour (♔…) remplis de brun-noir profond,
  // plus fins et élégants que les glyphes pleins sombres.
  const solid = { K: "♚", Q: "♛", R: "♜", B: "♝", N: "♞", P: "♟" };
  const outline = { k: "♔", q: "♕", r: "♖", b: "♗", n: "♘", p: "♙" };
  return solid[p] || outline[p] || "";
}



function escapeHtml(s) {
  // Échappe les caractères dangereux pour toute injection en innerHTML
  // (noms de joueurs contrôlés par l'utilisateur → prévention XSS).
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll("\"", "&quot;")
    .replaceAll("'", "&#39;");
}

function fenToGrid(fen) {
  // Convertit le champ de placement d'un FEN en grille 8x8 (rangée 8 en haut),
  // même format que le champ board de l'état web : symbole FEN ou ".".
  const placement = fen.split(" ")[0];
  const rows = placement.split("/");
  return rows.map((row) => {
    const cells = [];
    for (const ch of row) {
      if (ch >= "1" && ch <= "8") {
        for (let i = 0; i < Number(ch); i++) cells.push(".");
      } else {
        cells.push(ch);
      }
    }
    return cells;
  });
}

function evalWhiteToText(evalWhite) {
  // Centipions (perspective blancs) en texte : +1.2, -0.8, M3, -M1.
  if (evalWhite >= 90000) return "M" + Math.max(1, Math.round((100000 - evalWhite) / 100));
  if (evalWhite <= -90000) return "-M" + Math.max(1, Math.round((100000 + evalWhite) / 100));
  const v = evalWhite / 100;
  return (v > 0 ? "+" : "") + v.toFixed(1);
}

function evalToGaugePct(evalWhite) {
  // Centipions (perspective blancs) -> pourcentage de hauteur « blancs » (0..100)
  // pour la jauge verticale façon lichess (issue #89) : sigmoïde, 50 à
  // l'égalité, ~100 % mat blanc, ~0 % mat noir. null si eval indisponible.
  if (typeof evalWhite !== "number" || !isFinite(evalWhite)) return null;
  const cp = Math.max(-100000, Math.min(100000, evalWhite));
  return 50 * (1 + Math.tanh(cp / 400));
}

function seatDisplayName(name, color) {
  // Nom affiché pour un camp (issue #89) : « Libre » si le camp n'a pas de
  // joueur (nom vide ou nom par défaut « Blancs »/« Noirs »).
  const def = color === "white" ? "Blancs" : "Noirs";
  return (name && String(name) !== def) ? String(name) : "Libre";
}

function gaugeEvalCp(state, viewPly, isLiveFlag) {
  // Issue #94 : choisit l'eval (centipions) à afficher sur la jauge pour la
  // position affichée. Priorités :
  //  1. mode Analyse (analyse publiée) : UNIQUEMENT le PGN d'analyse
  //     (analysis.evals[k-1]) — jamais le cache live ;
  //  2. partie en cours, spectateur : historique live par pli live_evals[k-1] ;
  //  3. position courante sans analyse : live_eval (eval temps réel).
  // Retourne {evalCp: number|null, hasData: bool} — hasData=false => jauge masquée.
  const analysis = state && state.analysis;
  const live = state && state.live_eval;
  const liveEvals = (state && state.live_evals) || {};
  const moves = (state && state.moves) || [];
  const hasAnalysis = !!(analysis && analysis.evals && analysis.evals.length);
  const hasLive = !!(live && typeof live.eval_white === "number");
  const hasHist = Object.keys(liveEvals).length > 0;
  const hasData = hasAnalysis || hasLive || hasHist;
  const k = (viewPly === null || isLiveFlag) ? moves.length : viewPly;
  let evalCp = null;
  if (hasAnalysis && k >= 1 && k <= analysis.evals.length) {
    evalCp = analysis.evals[k - 1].eval_white;
  } else if (k >= 1 && liveEvals[String(k - 1)] !== undefined) {
    evalCp = liveEvals[String(k - 1)];
  } else if ((viewPly === null || isLiveFlag) && hasLive) {
    evalCp = live.eval_white;
  }
  return { evalCp, hasData };
}

function gaugeTextStyle(evalCp, myWhite) {
  // Issue #89 (retours 6-7) : le texte de la jauge s'affiche DU CÔTÉ de la
  // couleur favorisée, sans signe : noir côté blanc, blanc côté noir.
  // « Côté blanc » = en bas en vue des blancs, en haut en vue des noirs
  // (la barre s'oriente comme l'échiquier : camp de la perspective en bas).
  const whiteFavored = (evalCp === null || evalCp >= 0);
  const whiteSide = myWhite ? "bottom" : "top";
  const pos = whiteFavored ? whiteSide : (whiteSide === "top" ? "bottom" : "top");
  const color = whiteFavored ? "#262421" : "#f5f5f5";
  const text = evalWhiteToText(evalCp === null ? 0 : evalCp).replace(/^[+-]/, "");
  return { pos, color, text };
}

if (typeof module !== "undefined") {
  module.exports = { isStaleResponse, pieceGlyph, escapeHtml, fenToGrid,
                     evalWhiteToText, evalToGaugePct, seatDisplayName,
                     gaugeTextStyle, gaugeEvalCp };
}
