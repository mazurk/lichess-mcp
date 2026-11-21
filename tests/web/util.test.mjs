import assert from "node:assert/strict";
import test from "node:test";
import { isStaleResponse, pieceGlyph, escapeHtml, fenToGrid, evalWhiteToText,
         evalToGaugePct, seatDisplayName, gaugeTextStyle, gaugeEvalCp } from "../../web/util.js";

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

test("evalToGaugePct : égalité -> 50 %", () => {
  assert.equal(evalToGaugePct(0), 50);
});

test("evalToGaugePct : mat blanc -> ~100 %, mat noir -> ~0 %", () => {
  assert.ok(evalToGaugePct(99900) > 99.9);
  assert.ok(evalToGaugePct(-99900) < 0.1);
});

test("evalToGaugePct : +4 pions -> ~88 %, -4 pions -> ~12 % (sigmoïde lichess)", () => {
  assert.ok(Math.abs(evalToGaugePct(400) - 88) < 1);
  assert.ok(Math.abs(evalToGaugePct(-400) - 12) < 1);
  // symétrie
  assert.ok(Math.abs(evalToGaugePct(250) + evalToGaugePct(-250) - 100) < 1e-9);
});

test("evalToGaugePct : entrée invalide -> null (jauge masquée)", () => {
  assert.equal(evalToGaugePct(null), null);
  assert.equal(evalToGaugePct("abc"), null);
  assert.equal(evalToGaugePct(NaN), null);
  assert.equal(evalToGaugePct(undefined), null);
});

test("seatDisplayName : nom valide conservé, sinon « Libre » (issue #89)", () => {
  assert.equal(seatDisplayName("Alice", "white"), "Alice");
  assert.equal(seatDisplayName("Bob", "black"), "Bob");
  assert.equal(seatDisplayName("Blancs", "white"), "Libre");
  assert.equal(seatDisplayName("Noirs", "black"), "Libre");
  assert.equal(seatDisplayName("", "white"), "Libre");
  assert.equal(seatDisplayName(undefined, "black"), "Libre");
});

test("gaugeTextStyle : texte du côté de la couleur favorisée, sans signe (issue #89 retours 6-7)", () => {
  // éval +1.5, vue des blancs -> « 1.5 » en noir, en bas (côté blanc = bas en vue blancs)
  assert.deepEqual(gaugeTextStyle(150, true), { pos: "bottom", color: "#262421", text: "1.5" });
  // éval -0.8, vue des blancs -> « 0.8 » en blanc, en haut (côté noir = haut en vue blancs)
  assert.deepEqual(gaugeTextStyle(-80, true), { pos: "top", color: "#f5f5f5", text: "0.8" });
  // éval +1.5, vue des noirs -> « 1.5 » en noir, en haut (côté blanc = haut en vue noirs)
  assert.deepEqual(gaugeTextStyle(150, false), { pos: "top", color: "#262421", text: "1.5" });
  // éval -0.8, vue des noirs -> « 0.8 » en blanc, en bas (côté noir = bas en vue noirs)
  assert.deepEqual(gaugeTextStyle(-80, false), { pos: "bottom", color: "#f5f5f5", text: "0.8" });
  // mat : « M3 » sans signe
  assert.equal(gaugeTextStyle(99700, true).text, "M3");
  assert.equal(gaugeTextStyle(-99700, true).text, "M3");
  // égalité / eval indisponible -> côté blanc, « 0.0 »
  assert.deepEqual(gaugeTextStyle(0, true), { pos: "bottom", color: "#262421", text: "0.0" });
  assert.deepEqual(gaugeTextStyle(null, false), { pos: "top", color: "#262421", text: "0.0" });
});

test("gaugeEvalCp : mode Analyse = PGN d'analyse UNIQUEMENT, jamais le cache live (issue #94)", () => {
  const state = {
    moves: ["e4", "e5"],
    analysis: { evals: [{ eval_white: 30 }, { eval_white: -470 }] }, // analyse publiée
    live_eval: { eval_white: -80 },   // cache live (valeur différente, à ignorer)
    live_evals: { "0": 30, "1": -80 },
  };
  // position courante (live) : l'analyse gagne sur le cache live
  assert.deepEqual(gaugeEvalCp(state, null, true), { evalCp: -470, hasData: true });
  // navigation au pli 1 (après e4) : eval de l'analyse, pas live_evals
  assert.deepEqual(gaugeEvalCp(state, 1, false), { evalCp: 30, hasData: true });
});

test("gaugeEvalCp : partie en cours, spectateur — live_eval en DIRECT, live_evals en navigation (issue #94)", () => {
  const state = {
    moves: ["e4", "e5"],
    live_eval: { eval_white: 52 },
    live_evals: { "0": 40, "1": 52 },
  };
  // direct : eval live de la position courante (mise à jour à chaque calcul)
  assert.deepEqual(gaugeEvalCp(state, null, true), { evalCp: 52, hasData: true });
  // navigation : evals des positions passées (plus à 0)
  assert.deepEqual(gaugeEvalCp(state, 1, false), { evalCp: 40, hasData: true });
  assert.deepEqual(gaugeEvalCp(state, 2, false), { evalCp: 52, hasData: true });
  // position initiale : pas d'eval, mais jauge visible (neutre)
  assert.deepEqual(gaugeEvalCp(state, 0, false), { evalCp: null, hasData: true });
});

test("gaugeEvalCp : anti-régression décalage d'un demi-coup (issue #94 vague 4)", () => {
  // En direct, la jauge doit lire live_eval (position APRÈS le dernier coup),
  // JAMAIS live_evals[k-1] (position précédente) — même si les deux existent.
  const state = {
    moves: ["e4", "e5", "Nf3"],
    live_eval: { eval_white: 706 },          // eval après 3. Nf3 (position courante)
    live_evals: { "0": 40, "1": 52, "2": 559 }, // position précédente = 559
  };
  assert.equal(gaugeEvalCp(state, null, true).evalCp, 706);   // direct -> live_eval
  assert.equal(gaugeEvalCp(state, 3, false).evalCp, 559);     // navigation pli 3 -> live_evals[2]
  assert.equal(gaugeEvalCp(state, 2, false).evalCp, 52);
});

test("gaugeEvalCp : aucune donnée -> jauge masquée", () => {
  assert.deepEqual(gaugeEvalCp({ moves: [] }, null, true), { evalCp: null, hasData: false });
  assert.deepEqual(gaugeEvalCp({ moves: ["e4"], live_eval: null, live_evals: {} }, null, true),
    { evalCp: null, hasData: false });
});
