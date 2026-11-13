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
    "startPolling", "stopPolling", "refresh", "newGame", "joinGame",
    "playMove", "onSquare", "sideName", "pieceGlyph", "isStaleResponse",
    "escapeHtml", "api",
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
    assert 'src="pieces.js"' in HTML


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
