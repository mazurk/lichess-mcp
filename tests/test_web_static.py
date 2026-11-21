"""Garde-fous statiques sur la page web (fonctions définies, routes servies)."""

from pathlib import Path

REPO = Path(__file__).parent.parent
HTML = (REPO / "web" / "index.html").read_text(encoding="utf-8")
APP_JS = (REPO / "web" / "app.js").read_text(encoding="utf-8")
UTIL = (REPO / "web" / "util.js").read_text(encoding="utf-8")

# Toutes les fonctions appelées par la page doivent être définies
# (dans le script inline ou dans util.js) — régression du type
# « setStatus is not defined » qui cassait tout le rendu.
PAGE_FUNCTIONS = [
    "setStatus", "setResult", "afterState", "render", "renderBoardLabels",
    "renderEvalGauge", "startPolling", "stopPolling", "refresh", "newGame",
    "joinGame", "playMove", "onSquare", "sideName", "pieceGlyph",
    "isStaleResponse", "escapeHtml", "api", "gaugeTextStyle",
]


def test_live_eval_wired_in_web_and_server():
    """Issue #94 : jauge temps réel spectateurs — live_eval câblé côté front
    (spectator=1, rendu jauge) et outil MCP spectator_analysis côté serveur."""
    assert "spectator=1" in APP_JS          # le spectateur web demande l'eval live
    assert "state.live_eval" in APP_JS      # la jauge la consomme en direct
    srv = (REPO / "server.py").read_text(encoding="utf-8")
    assert "def spectator_analysis(" in srv
    assert "def _maybe_live_eval(" in srv
    # fair-play : live_eval renvoyée uniquement si spectator=1
    assert '"live_eval": game.get("live_eval") if spectator else None' in srv
    # issue #94 retour : snapshot atomique + historique live par pli
    assert ".copy(stack=True)" in srv
    assert '"live_evals"' in srv
    # front : mode Analyse = PGN d'analyse UNIQUEMENT (jamais le cache live en
    # priorité), puis historique live, puis eval live courante
    assert "hasAnalysis && k >= 1" in APP_JS
    assert "liveEvals[String(k - 1)]" in APP_JS
    # issue #94 vague 3 : moteur live persistant + profondeur réellement atteinte
    analysis_py = (REPO / "analysis.py").read_text(encoding="utf-8")
    assert "_live_stockfish" in analysis_py and "depth_reached" in analysis_py
    # legal_moves : jamais de coup omis (SAN sûr, repli UCI)
    assert "def _san_safe(" in srv and '"count"' in srv


def test_all_page_functions_defined():
    for fn in PAGE_FUNCTIONS:
        defined = (
            f"function {fn}(" in APP_JS
            or f"function {fn} (" in APP_JS
            or f"const {fn} =" in APP_JS
            or f"function {fn}(" in UTIL
        )
        assert defined, (
            f"La page appelle {fn}() mais cette fonction n'est définie "
            "ni dans index.html ni dans util.js"
        )


def test_util_js_route_served(client):
    r = client.get("/util.js")
    assert r.status_code == 200
    assert "javascript" in r.headers.get("content-type", "")
    assert "isStaleResponse" in r.text


def test_util_js_route_served_without_auth(client, monkeypatch):
    monkeypatch.setenv("LICHESS_MCP_AUTH_TOKEN", "secret-token")
    r = client.get("/util.js")
    assert r.status_code == 200


def test_pieces_js_route_served(client):
    r = client.get("/pieces.js")
    assert r.status_code == 200
    assert "javascript" in r.headers.get("content-type", "")
    assert "PIECE_SVG" in r.text
    assert "wK" in r.text and "bP" in r.text


def test_index_loads_pieces_js():
    import re as _re
    assert _re.search(r'src="pieces\.js(\?v=[\w]+)?"', HTML)


def test_all_piece_svgs_embedded():
    for p in ["wK","wQ","wR","wB","wN","wP","bK","bQ","bR","bB","bN","bP"]:
        assert p in (REPO / "web" / "pieces.js").read_text(encoding="utf-8")


def test_app_js_route_served(client):
    r = client.get("/app.js")
    assert r.status_code == 200
    assert "javascript" in r.headers.get("content-type", "")
    assert "function render(" in r.text


def test_index_has_no_inline_script():
    # CSP strict (script-src 'self') : le script doit être externe
    assert "<script>\n" not in HTML and "<script>\"" not in HTML


def test_version_badge_present_in_page_and_js():
    """Badge de version en coin haut gauche (issue #67) : élément + CSS dans
    index.html, chargement via /web/api/version dans app.js."""
    assert 'id="versionBadge"' in HTML
    assert "#versionBadge" in HTML  # position: fixed, coin haut gauche
    assert "/web/api/version" in APP_JS
    assert "loadVersionBadge" in APP_JS


def test_eval_gauge_present_in_page_and_js():
    """Jauge d'évaluation verticale à droite de l'échiquier (issue #89) :
    élément + CSS dans index.html, rendu + fonction de conversion dans app.js/util.js."""
    assert 'id="evalgauge"' in HTML
    assert 'id="evalgauge-fill"' in HTML
    assert ".evalgauge" in HTML  # CSS de la jauge
    assert "function renderEvalGauge(" in APP_JS
    assert "evalToGaugePct" in APP_JS and "evalToGaugePct" in UTIL


def test_analysis_mode_button_is_consulter():
    """Issue #89 : le bouton du mode analyse s'appelle « Consulter » (il ouvre
    une analyse déjà produite, ne la lance pas)."""
    assert 'analysis: "Consulter"' in APP_JS
    assert 'analysis: "Analyser"' not in APP_JS


def test_game_select_opens_game_directly():
    """Issue #89 : sélectionner une partie dans la liste l'ouvre directement
    (plus besoin de cliquer ensuite sur le bouton d'action)."""
    assert 'getElementById("gameId").onchange' in APP_JS


def test_seat_labels_plain_name_and_libre():
    """Issue #89 : libellés de camp = repère couleur + nom seul ; « Libre » si
    le camp est vide (util.js seatDisplayName, utilisé par app.js)."""
    assert "seatDisplayName" in UTIL and '"Libre"' in UTIL
    assert "seatDisplayName" in APP_JS
    assert "seat-dot" in HTML  # repère léger de la couleur du camp
    # l'ancien préfixe « Blancs — <nom> » des libellés de camp est supprimé
    assert '"Blancs" : "Noirs") + " — "' not in APP_JS
    assert '(color === "white" ? "Blancs" : "Noirs") + " — "' not in APP_JS


def test_eval_gauge_text_small_and_contrast():
    """Issue #89 retours 6-7 : texte de la jauge assez petit (9px), barre
    orientée comme l'échiquier (fill du camp de la perspective ancré EN BAS),
    texte du côté de la couleur favorisée sans signe (gaugeTextStyle)."""
    assert "font-size: 9px" in HTML  # .evalgauge-text réduit
    assert "gaugeTextStyle" in APP_JS and "gaugeTextStyle" in UTIL
    assert "bottom: 0" in HTML  # .evalgauge-fill ancré en bas (retour 6)
    assert "gaugeTextStyle(evalCp, myWhite)" in APP_JS  # retour 7 câblé


def test_analysis_view_color_applies_immediately():
    """Issue #89 retour : en mode Analyse, changer la perspective ré-ouvre la
    partie immédiatement (sans clic sur « Consulter »)."""
    assert 'getElementById("viewColor").onchange' in APP_JS


def test_game_list_refreshes_periodically():
    """Issue #89 retour : la liste des parties est rafraîchie périodiquement
    (~0,5 Hz = toutes les 2 s) ; le bouton ↻ manuel reste en complément."""
    assert "setInterval" in APP_JS and "refreshGameList" in APP_JS
    assert "2000" in APP_JS
