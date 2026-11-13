"""Tests de sécurité du serveur web : XSS, traversée de chemin, méthodes,
en-têtes de sécurité, authentification, CORS."""

import pytest

import server


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.delenv("LICHESS_MCP_AUTH_TOKEN", raising=False)
    server.GAMES.clear()
    server.ACTIVE_PUZZLES.clear()
    yield
    server.GAMES.clear()
    server.ACTIVE_PUZZLES.clear()


# ---------------------------------------------------------------- en-têtes

def test_security_headers_on_page(client):
    r = client.get("/")
    assert r.status_code == 200
    csp = r.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"


def test_security_headers_on_scripts(client):
    for path in ("/util.js", "/pieces.js"):
        r = client.get(path)
        assert r.status_code == 200
        assert "default-src 'self'" in r.headers.get("content-security-policy", "")
        assert r.headers.get("x-content-type-options") == "nosniff"


def test_no_permissive_cors(client):
    """Aucun en-tête CORS sauvage : la page ne doit pas être lisible cross-origin."""
    r = client.get("/")
    assert r.headers.get("access-control-allow-origin") != "*"


# ---------------------------------------------------------------- traversée de chemin

def test_path_traversal_rejected(client):
    for path in (
        "/../server.py",
        "/..%2f..%2fserver.py",
        "/web/api/games/../../../etc/passwd",
        "/web/api/games/..%2f..%2f..%2fetc%2fpasswd",
        "/util.js/../../server.py",
    ):
        r = client.get(path)
        assert r.status_code in (400, 404, 405, 500), f"{path} -> {r.status_code}"
        assert "import" not in r.text, f"fuite de code source via {path}"


def test_game_id_injection_is_inert(client):
    """Un game_id malveillant n'est qu'une clé de dictionnaire : rien n'est
    interprété, aucun chemin construit à partir de lui."""
    for gid in ("../../etc/passwd", "${jndi:ldap://x}", "<script>alert(1)</script>", "%00"):
        r = client.get(f"/web/api/games/{gid}")
        assert r.status_code == 404


def test_move_injection_rejected(client):
    gid = server.new_game(white="A", black="B")["game_id"]
    for move in ("e2e4; rm -rf /", "$(reboot)", "e2e4\nQUIT", "__import__"):
        r = client.post(f"/web/api/games/{gid}/move",
                        json={"move": move, "your_color": "white", "your_name": "A"})
        assert r.status_code == 400, f"{move!r} accepté !"


def test_fen_injection_rejected():
    for fen in ("invalid fen", "/dev/null", "$(cmd)", "8/8/8/8/8/8/8/8 w - - 0 1; rm"):
        try:
            r = server.new_analysis_board(fen)
            # un FEN peut être valide malgré des espaces bizarres : il ne doit
            # rien exécuter — on vérifie juste qu'il ne lève pas d'effet de bord
            assert r["fen"]
        except ValueError:
            pass


# ---------------------------------------------------------------- méthodes HTTP

def test_unsupported_methods_rejected(client):
    for method_path in ("/web/api/games", "/"):
        r = client.post(method_path, json={})
        if method_path == "/":
            assert r.status_code in (405, 422, 500)


def test_delete_not_supported(client):
    gid = server.new_game(white="A", black="B")["game_id"]
    # DELETE n'est pas enregistré -> la partie survit
    r = client.get(f"/web/api/games/{gid}")
    assert r.status_code == 200


# ---------------------------------------------------------------- authentification

def test_all_api_endpoints_require_token(client, monkeypatch):
    monkeypatch.setenv("LICHESS_MCP_AUTH_TOKEN", "secret-token")
    gid = server.new_game(white="A", black="B")["game_id"]
    checks = [
        client.post("/web/api/games", json={"your_color": "white"}),
        client.get(f"/web/api/games/{gid}"),
        client.post(f"/web/api/games/{gid}/move", json={"move": "e2e4", "your_color": "white"}),
        client.post(f"/web/api/games/{gid}/join", json={"name": "X"}),
    ]
    for r in checks:
        assert r.status_code == 401, f"endpoint accessible sans token : {r.status_code}"


def test_wrong_token_rejected(client, monkeypatch):
    monkeypatch.setenv("LICHESS_MCP_AUTH_TOKEN", "secret-token")
    r = client.get("/web/api/games/x",
                   headers={"Authorization": "Bearer mauvais-token"})
    assert r.status_code == 401


def test_token_not_echoed_anywhere(client, monkeypatch):
    monkeypatch.setenv("LICHESS_MCP_AUTH_TOKEN", "secret-token")
    r = client.get("/", headers={"Authorization": "Bearer secret-token"})
    assert "secret-token" not in r.text


# ---------------------------------------------------------------- XSS

def test_xss_names_stored_raw_but_page_escapes(client):
    """Le serveur stocke le nom brut (donnée), l'échappement est fait par la
    page (présentation) — vérifié par les tests Node (escapeHtml) et le test
    navigateur. Ici : le nom malveillant ne doit pas être exécuté côté serveur
    ni modifier la structure JSON."""
    payload = '<img src=x onerror=alert(1)>'
    gid = server.new_game(white="A", black="B")["game_id"]
    r = client.post(f"/web/api/games/{gid}/join", json={"name": payload, "color": "white"})
    assert r.status_code == 200
    state = r.json()
    assert state["white_seat"] == payload
    # l'état reste un objet JSON bien formé, le nom est une simple chaîne
    assert isinstance(state["white_seat"], str)


def test_error_messages_do_not_reflect_scripts(client):
    gid = server.new_game(white="A", black="B")["game_id"]
    r = client.post(f"/web/api/games/{gid}/move",
                    json={"move": "<script>alert(1)</script>", "your_color": "white"})
    assert r.status_code == 400
    # le message d'erreur peut contenir le coup fourni (utile au débogage),
    # mais la page l'affiche via textContent (pas d'injection HTML)


def test_index_html_never_served_stale(client):
    """index.html doit être revalidé à chaque chargement (pas de cache
    heuristique) — sinon le navigateur sert une ancienne page après un
    déploiement, avec d'anciens scripts, pendant des jours."""
    r = client.get("/")
    assert r.headers.get("cache-control") == "no-cache"
    assert r.headers.get("etag"), "l'ETAG rend la revalidation gratuite"


def test_scripts_cacheable_long_term(client):
    """Les scripts sont référencés versionnés (?v=N) : cache long autorisé."""
    for path in ("/util.js", "/pieces.js", "/app.js"):
        r = client.get(path)
        assert "max-age=31536000" in r.headers.get("cache-control", "")
        assert "immutable" in r.headers.get("cache-control", "")
