"""Tests du cycle de vie des issues GitHub (3 états : open / developped / closed).

Vérifie que le workflow `.github/workflows/label-developped.yml` pose bien le
label `developped` au merge d'une PR **sans jamais fermer l'issue**, et que
AGENTS.md documente ce cycle de vie.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "label-developped.yml"
AGENTS = REPO / "AGENTS.md"


def test_label_developped_workflow_exists():
    """Le workflow de labellisation existe."""
    assert WORKFLOW.is_file(), "workflow label-developped.yml manquant"


def test_workflow_triggers_on_merged_pr():
    """Le workflow se déclenche quand une PR est fermée (et mergée), plus en
    manuel (workflow_dispatch) pour rejouer sur une PR donnée."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request:" in text
    assert "types: [closed]" in text
    assert "workflow_dispatch:" in text
    assert "github.event.pull_request.merged == true" in text


def test_workflow_has_issues_write_permission():
    """Le workflow a le droit d'écrire sur les issues (label + commentaire)."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "issues: write" in text
    assert "pull-requests: read" in text


def test_workflow_labels_but_never_closes():
    """Le workflow pose le label `developped` mais ne ferme JAMAIS l'issue :
    aucun appel à update issue avec state: closed."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "labels: ['developped']" in text
    assert "addLabels" in text
    assert "state: 'closed'" not in text
    assert 'state: "closed"' not in text
    assert "issues.update" not in text


def test_agents_md_documents_three_issue_states():
    """AGENTS.md documente les 3 états : open / developped / closed."""
    text = AGENTS.read_text(encoding="utf-8")
    assert "open" in text and "developped" in text and "closed" in text
    assert "label-developped.yml" in text
    # pratique : l'agent ne ferme pas les issues, c'est Nicolas qui valide
    assert "ne ferme" in text


def test_agents_md_requires_issue_before_any_bugfix():
    """Règle « bug = issue d'abord » : un bug sans issue GitHub doit d'abord
    donner lieu à une issue créée AVANT le correctif (état open)."""
    text = AGENTS.read_text(encoding="utf-8")
    assert "Bug = issue d'abord" in text
    assert "la créer AVANT de commencer" in text
    assert "Aucun correctif de bug sans issue préalable" in text
    # la doc utilisateur reflète la même règle
    issues_doc = (REPO / "docs" / "ISSUES.md").read_text(encoding="utf-8")
    assert "Bug = issue d'abord" in issues_doc


def test_agents_md_documents_connector_update_rule():
    """Règle « connecteur MCP à mettre à jour » : quand une livraison change les
    outils exposés, je dois le dire dans l'issue ET dans le chat (AGENTS.md)."""
    text = AGENTS.read_text(encoding="utf-8")
    assert "Connecteur MCP à mettre à jour" in text
    assert "dans l'issue" in text and "dans le chat" in text
    assert "Studio Mistral" in text
