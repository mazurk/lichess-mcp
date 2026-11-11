"""Vérifie que l'image Docker embarque tout ce que le serveur importe/sert.

Empêche les régressions du type « fichier ajouté au dépôt mais oublié dans le
Dockerfile » (déjà vu pour web/ et analysis.py).
"""

from pathlib import Path

REPO = Path(__file__).parent.parent


def test_dockerfile_copies_all_python_modules():
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    for module in REPO.glob("*.py"):
        assert module.name in dockerfile, (
            f"{module.name} est absent du Dockerfile — le conteneur plantera "
            "au démarrage (ModuleNotFoundError)"
        )


def test_dockerfile_copies_web_dir_and_puzzles():
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert "web/" in dockerfile, "le dossier web/ doit être copié dans l'image"
    assert "puzzles.json" in dockerfile, "puzzles.json doit être copié dans l'image"


def test_web_index_exists():
    assert (REPO / "web" / "index.html").is_file()


def test_caddyfile_example_committed_and_local_ignored():
    assert (REPO / "Caddyfile.example").is_file(), (
        "Caddyfile.example (modèle versionné) doit exister"
    )
    gitignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "Caddyfile" in gitignore, (
        "Caddyfile (config locale personnalisée) doit être ignoré par git "
        "pour survivre aux git pull"
    )
