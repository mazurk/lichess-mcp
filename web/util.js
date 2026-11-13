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

if (typeof module !== "undefined") {
  module.exports = { isStaleResponse, pieceGlyph, escapeHtml };
}
