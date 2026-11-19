import assert from "node:assert/strict";
import test from "node:test";
import { isStaleResponse, pieceGlyph, escapeHtml, fenToGrid, evalWhiteToText } from "../../web/util.js";

const state = (gameId, moves) => ({ game_id: gameId, moves });

test("isStaleResponse : réponse plus ancienne (course poll/coup) -> périmée", () => {
  const current = state("abcd", ["e4", "e5", "Nf3"]);
  const fetched = state("abcd", ["e4", "e5"]);
  assert.equal(isStaleResponse(current, fetched), true);
});

test("isStaleResponse : réponse à jour -> acceptée", () => {
  const current = state("abcd", ["e4", "e5"]);
  const fetched = state("abcd", ["e4", "e5", "Nf3"]);
  assert.equal(isStaleResponse(current, fetched), false);
});

test("isStaleResponse : même nombre de coups -> acceptée", () => {
  const current = state("abcd", ["e4"]);
  const fetched = state("abcd", ["e4"]);
  assert.equal(isStaleResponse(current, fetched), false);
});

test("isStaleResponse : autre partie -> périmée", () => {
  const current = state("abcd", ["e4"]);
  const fetched = state("wxyz", ["e4", "e5"]);
  assert.equal(isStaleResponse(current, fetched), true);
});

test("isStaleResponse : réponse absente ou malformée -> périmée", () => {
  assert.equal(isStaleResponse(state("abcd", []), null), true);
  assert.equal(isStaleResponse(state("abcd", []), undefined), true);
  assert.equal(isStaleResponse(state("abcd", []), { game_id: "abcd" }), true);
});

test("isStaleResponse : état courant sans coups -> acceptée", () => {
  assert.equal(isStaleResponse(null, state("abcd", ["e4"])), true);
  assert.equal(isStaleResponse({ game_id: "abcd" }, state("abcd", ["e4"])), false);
});

test("pieceGlyph : glyphes pleins pour les blancs, contour pour les noirs", () => {
  assert.equal(pieceGlyph("K"), "♚");
  assert.equal(pieceGlyph("Q"), "♛");
  assert.equal(pieceGlyph("P"), "♟");
  assert.equal(pieceGlyph("k"), "♔");
  assert.equal(pieceGlyph("q"), "♕");
  assert.equal(pieceGlyph("p"), "♙");
});

test("pieceGlyph : case vide ou inconnue -> chaîne vide", () => {
  assert.equal(pieceGlyph("."), "");
  assert.equal(pieceGlyph("x"), "");
});

test("escapeHtml : neutralise les payloads XSS classiques", () => {
  assert.equal(escapeHtml('<img src=x onerror=alert(1)>'),
    "&lt;img src=x onerror=alert(1)&gt;");
  assert.equal(escapeHtml("<script>alert('XSS')</script>"),
    "&lt;script&gt;alert(&#39;XSS&#39;)&lt;/script&gt;");
  assert.equal(escapeHtml('" onmouseover="alert(1)'), "&quot; onmouseover=&quot;alert(1)");
});

test("escapeHtml : les noms normaux sont inchangés", () => {
  assert.equal(escapeHtml("Alice"), "Alice");
  assert.equal(escapeHtml("Agent IA"), "Agent IA");
});

test("escapeHtml : idempotent sur du texte déjà sûr", () => {
  assert.equal(escapeHtml("a & b"), "a &amp; b");
  assert.equal(escapeHtml(escapeHtml("<b>")), "&amp;lt;b&amp;gt;");
});

test("fenToGrid : position initiale", () => {
  const grid = fenToGrid("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1");
  assert.equal(grid.length, 8);
  assert.equal(grid[0][0], "r");
  assert.equal(grid[0][4], "k");
  assert.equal(grid[7][4], "K");
  assert.equal(grid[4][4], ".");
});

test("fenToGrid : cases vides compactées", () => {
  const grid = fenToGrid("8/8/8/3p4/8/8/8/8 w - - 0 1");
  assert.equal(grid[3][3], "p");
  assert.equal(grid[3].filter((c) => c === ".").length, 7);
});

test("evalWhiteToText : centipions et mats", () => {
  assert.equal(evalWhiteToText(120), "+1.2");
  assert.equal(evalWhiteToText(-80), "-0.8");
  assert.equal(evalWhiteToText(0), "0.0");
  assert.equal(evalWhiteToText(99900), "M1");
  assert.equal(evalWhiteToText(-99900), "-M1");
});
