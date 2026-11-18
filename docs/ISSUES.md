# Cycle de vie des issues (4 états)

| État | Signification | Qui |
|---|---|---|
| **open** | Issue à réaliser | — |
| **developped** | Issue **mergée dans `main`** (PR validée) — label `developped` posé automatiquement au merge par `.github/workflows/label-developped.yml` ; l'issue **reste ouverte** | Agent (automatique au merge de la PR) |
| **deployed** | Issue corrigée, validée par l'agent **et déployée sur la Raspberry Pi** (vérifiée : HTTPS 200 + smoke test) — label `deployed` posé par l'agent après le déploiement | Agent (manuel, post-déploiement) |
| **closed** | Correction **validée par Nicolas** | Nicolas (fermeture manuelle) |

Règles :

- **Bug = issue d'abord** : tout bug corrigé a son issue créée **avant** le
  correctif (état `open`), même s'il est remonté à la volée. Aucun correctif
  de bug sans issue préalable.
- Une PR référence les issues traitées via `#N` dans son titre ou sa description
  (ex. « Issues #49 #50 #54 #55 : ... »).
- Au merge de la PR, le workflow pose le label `developped` + un commentaire sur
  chaque issue référencée. **Aucune issue n'est jamais fermée automatiquement.**
- Filtres utiles : `label:deployed is:open` = déployées sur la Pi, en attente de
  validation ; `label:developped is:open -label:deployed` = mergées mais pas
  encore déployées ; `is:open` = à faire ou en cours ; `is:closed` = validées.
- Le workflow est rejouable manuellement : *Actions → Label developped → Run
  workflow* (input `pr_number`).
