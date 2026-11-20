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

function gaugeTextPlacement(topPct, myWhite) {
  // Issue #89 (retour 5) : le texte de la jauge est TOUJOURS en haut de la
  // barre, quelle que soit la position de la frontière blanc/noir. Sa couleur
  // est l'inverse de la portion du haut (fill si frontière sous la moitié,
  // fond sinon) — contraste garanti. Le fill (haut) = camp de la perspective :
  // blanc si myWhite.
  const fillIsWhite = myWhite;
  const topIsFill = topPct >= 50;
  const color = topIsFill
    ? (fillIsWhite ? "#262421" : "#f5f5f5")
    : (fillIsWhite ? "#f5f5f5" : "#262421");
  return { inTop: true, color };
}

if (typeof module !== "undefined") {
  module.exports = { isStaleResponse, pieceGlyph, escapeHtml, fenToGrid,
                     evalWhiteToText, evalToGaugePct, seatDisplayName,
                     gaugeTextPlacement };
}
