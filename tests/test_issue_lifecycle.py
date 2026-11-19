"""Tests du cycle de vie des issues GitHub (4 états : open / developped / deployed / closed).

Vérifie que le workflow `.github/workflows/label-developped.yml` pose bien le
label `developped` au merge d'une PR **sans jamais fermer l'issue**, et que
docs/ISSUES.md documente ce cycle de vie (AGENTS.md, le manuel de l'agent,
vit désormais dans le dépôt `mazurk/agents` — hors de ce dépôt public).
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "label-developped.yml"
ISSUES_DOC = REPO / "docs" / "ISSUES.md"


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


def test_issues_doc_documents_four_issue_states():
    """docs/ISSUES.md documente les 4 états : open / developped / deployed / closed."""
    text = ISSUES_DOC.read_text(encoding="utf-8")
    for state in ("open", "developped", "deployed", "closed"):
        assert state in text, f"état {state} manquant dans docs/ISSUES.md"
    assert "label-developped.yml" in text
    # pratique : l'agent ne ferme pas les issues, c'est le propriétaire qui valide
    assert "jamais fermée automatiquement" in text
    # pratique : l'agent pose lui-même le label deployed après le déploiement serveur
    assert "deployed" in text and "serveur de production" in text


def test_issues_md_documents_four_states():
    """docs/ISSUES.md documente aussi les 4 états et le filtre deployed."""
    text = (REPO / "docs" / "ISSUES.md").read_text(encoding="utf-8")
    for state in ("open", "developped", "deployed", "closed"):
        assert f"**{state}**" in text
    assert "label:deployed is:open" in text


def test_issues_doc_requires_issue_before_any_bugfix():
    """Règle « bug = issue d'abord » : un bug sans issue GitHub doit d'abord
    donner lieu à une issue créée AVANT le correctif (état open)."""
    text = " ".join(ISSUES_DOC.read_text(encoding="utf-8").split())
    assert "Bug = issue d'abord" in text
    assert "Aucun correctif de bug sans issue préalable" in text


def test_issues_doc_documents_connector_update_rule():
    """Règle « connecteur MCP à mettre à jour » : quand une livraison change les
    outils exposés, il faut le dire dans l'issue ET dans le chat."""
    text = ISSUES_DOC.read_text(encoding="utf-8")
    assert "Connecteur MCP à mettre à jour" in text
    assert "dans l'issue" in text and "dans le chat" in text
    assert "Studio Mistral" in text
