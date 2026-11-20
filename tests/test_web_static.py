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
    "isStaleResponse", "escapeHtml", "api",
]


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
