# Cycle de vie des issues (3 états)

| État | Signification | Qui |
|---|---|---|
| **open** | Issue créée, pas encore traitée | — |
| **developped** | Issue corrigée, validée par l'agent et **mergée dans `main`** (label `developped` posé automatiquement au merge par `.github/workflows/label-developped.yml` ; l'issue **reste ouverte**) | Agent (automatique au merge de la PR) |
| **closed** | Issue **validée par Nicolas** | Nicolas (fermeture manuelle) |

Règles :

- Une PR référence les issues traitées via `#N` dans son titre ou sa description
  (ex. « Issues #49 #50 #54 #55 : ... »).
- Au merge de la PR, le workflow pose le label `developped` + un commentaire sur
  chaque issue référencée. **Aucune issue n'est jamais fermée automatiquement.**
- Filtres utiles : `label:developped is:open` = livrées, en attente de validation ;
  `is:open` = à faire ou en cours ; `is:closed` = validées.
- Le workflow est rejouable manuellement : *Actions → Label developped → Run
  workflow* (input `pr_number`).
