# lichess-mcp

Serveur MCP d'échecs **hors ligne**, pensé pour l'agent Vibe de Mistral.
Aucune connexion à lichess.org (et donc aucun token) n'est nécessaire : les
parties se jouent en local contre un moteur intégré, et les puzzles suivent le
format Lichess (FEN + coups UCI).

## Fonctionnalités

- **Joueurs nommés** : chaque camp porte un nom (« Nicolas », « Agent IA »,
  « Moteur »...) — plus d'ambiguïté sur qui joue quoi. Quiconque connaît
  l'identifiant d'une partie peut jouer un camp via `play_move(color=...)` :
  deux agents, deux humains, ou un mélange.
- **Jouer contre le moteur** : `engine_color` désigne le camp joué par le
  moteur local (négamax alpha-beta, ou [Stockfish](https://stockfishchess.org/)
  s'il est installé — inclus dans l'image Docker).
- **Partie libre** (`engine_color=None`) : aucun moteur — les deux camps sont
  joués de l'extérieur (agents ou humains), comme une partie amicale Lichess.
- **Échiquier d'analyse (dry-run)** : `new_analysis_board(fen)` pour explorer
  une position en jouant les deux couleurs, avec `legal_moves`, `get_board`,
  `undo` et `analyze_game` — sans toucher aux parties en cours.
- **Analyse Stockfish** : `analyze_game` (partie ou échiquier d'analyse) et
  `analyze_pgn` (n'importe quel PGN) retournent un PGN annoté compatible
  Lichess (`[%eval ...]`, blunders/erreurs/imprécisions marqués) avec le
  meilleur coup à chaque erreur. `analyze_game` **publie** aussi l'analyse sur
  l'interface web (`publish=True`, défaut) : jauge d'évaluation par coup et
  coups gaffés colorés pour le debrief.
- **Règles complètes** gérées par [python-chess](https://github.com/niklasf/python-chess) :
  promotion, prise en passant, roc, pat, règle des 50 coups, répétition triple.
- **Puzzles** au format Lichess : mats en un ou deux coups à résoudre via
  l'agent (`next_puzzle`/`try_puzzle_move`), ou **publiés en mode puzzle**
  (`start_puzzle`) : l'échiquier est matérialisé sur le serveur, l'agent
  s'entraîne avec `try_puzzle_move(..., game_id=...)` (chaque coup validé
  apparaît en temps réel) et vous consultez la résolution sur l'interface web
  (mode **Puzzles**, avec la progression du puzzle).
- **Interface web** : plateau interactif dans le navigateur
  (`http://localhost:8000/`) — jouez à la souris, rejoignez une partie par son
  identifiant, ou regardez en **mode spectateur**. Rendu façon Lichess :
  **pièces SVG Cburnett** (celles de lichess.org, embarquées, 100 % hors
  ligne) et couleurs du plateau officiel.
- **Historique** : annulation d'un coup (+ réponse du moteur), abandon par camp, statut.
- **Parties persistantes** : l'état du serveur (parties, échiquiers d'analyse,
  puzzles publiés, sessions `next_puzzle`, alertes triche) est écrit en JSON à
  chaque mutation dans `data/server_state.json` et rechargé au démarrage :
  **les parties survivent à un redémarrage du serveur**. En Docker, `data/`
  est monté sur le volume nommé `games_data`. L'horloge est gelée pendant
  l'arrêt du serveur (le temps du redémarrage ne compte pas). Chemin
  surchargeable via `LICHESS_MCP_STATE_FILE`. Nettoyage : `delete_game`.

## Outils MCP exposés

| Outil | Rôle |
|---|---|
| `new_game(white, black, level, engine_color, clock)` | Démarre une partie : noms des deux joueurs ; `engine_color` = camp du moteur (`None` = partie libre). N'attribue aucune place. `clock="10+0"` : horloge — elle ne démarre que lorsque les **deux camps sont couverts** (joueur ayant rejoint ou moteur) ; le 1er coup des blancs est refusé tant que le camp noir n'est pas couvert |
| `join_game(game_id, player, color, spectate)` | Occupe un camp (premier arrivé, premier servi) ; le camp devient verrouillé au nom du joueur, l'autre joueur obtient l'autre camp. `spectate=True` : rejoint en spectateur (aucune place prise, nombre illimité, compteur visible sur l'interface) |
| `new_analysis_board(fen)` | Échiquier d'analyse (dry-run) : testez des coups des deux couleurs depuis un FEN |
| `get_board(game_id)` | Échiquier ASCII, FEN, trait, noms des joueurs |
| `legal_moves(game_id)` | Coups légaux au format SAN |
| `play_move(game_id, move, color, player)` | Joue un coup (SAN ou UCI) pour le camp `color` (défaut : camp au trait) ; refusé pour le camp du moteur. Un **SAN ambigu** (ex. « Nxd5 » jouable par deux cavaliers) est refusé avec la liste des coups valides — jamais de résolution silencieuse |
| `wait_for_turn(game_id, color, timeout_seconds)` | Long-poll : attend que ce soit le tour du camp `color` — jouer en temps réel sans notification |
| `undo(game_id)` | Annule le dernier coup (+ réponse du moteur s'il vient de jouer) |
| `resign(game_id, color, player)` | Abandon pour le camp indiqué (défaut : camp au trait) ou pour le **joueur nommé** (`player`, résolu via les places prises) ; le nom du vainqueur est toujours renvoyé |
| `game_status(game_id)` | Statut : joueurs, trait, fin éventuelle — un drapeau tombé est détecté à chaque lecture (`over` bascule, avec `result`/`winner`) |
| `get_moves(game_id, from_ply)` | Coups joués en SAN (+ UCI), dans l'ordre ; `from_ply` ne renvoie que la fin (index 0-based du 1er demi-coup) |
| `delete_game(game_id)` | Supprime définitivement une partie / un échiquier d'analyse / un puzzle publié (utile pour nettoyer les échiquiers de test) |
| `list_games(kind)` | Liste les parties du serveur sans besoin de l'identifiant (`kind` = `game`, `analysis`, `puzzle` ou None) : id, joueurs, trait, FEN, statut |
| `next_puzzle()` | Tire un puzzle (mat en 1 ou en 2) |
| `try_puzzle_move(puzzle_id, move, game_id)` | Propose une solution de puzzle — pour un mat en 2 : coup clé validé, réponse adverse jouée automatiquement, puis coup de mat. Avec `game_id` (puzzle publié), le coup validé est joué sur l'échiquier web |
| `list_puzzles(mate_in)` | Catalogue des puzzles disponibles (filtre 1 ou 2) — ids à passer à `start_puzzle` |
| `start_puzzle(puzzle_id, name)` | Publie un puzzle en mode **puzzle** (échiquier observable sur le web) pour entraîner l'agent |
| `analyze_game(game_id, depth)` | Analyse Stockfish d'une partie ou d'un échiquier d'analyse → PGN annoté (en-têtes complets : White/Black/Result/Date) + coups critiques. Si une partie/un puzzle est en cours, l'appel est signalé « triche détectée » (`cheat_detected`) et le bandeau s'affiche sous l'échiquier de la partie/puzzle concernée — sauf si l'échiquier avait déjà été analysé avant le début de la partie |
| `analyze_pgn(pgn, depth)` | Analyse Stockfish d'un PGN externe (ex. exporté de lichess.org) |

## Installation

```bash
git clone https://github.com/mazurk/lichess-mcp.git
cd lichess-mcp
python -m venv .venv && source .venv/bin/activate  # Windows : .venv\Scripts\activate
pip install -r requirements.txt
```

## Exécution

Le serveur fonctionne par défaut en **stdio** (le client MCP le lance et
communique via l'entrée/sortie standard) :

```bash
python server.py
```

Pour l'exposer en **HTTP** (ex. pour un agent cloud comme Vibe de Mistral) :

```bash
export LICHESS_MCP_TRANSPORT=http
export LICHESS_MCP_AUTH_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
python server.py   # écoute sur http://127.0.0.1:8000/mcp
```

Le déploiement complet (Docker + Caddy HTTPS + token, redirection de ports,
connexion au Studio Mistral) est détaillé dans
[docs/DEPLOIEMENT.md](docs/DEPLOIEMENT.md).

### Interface web (plateau navigateur)

En mode HTTP, le serveur expose aussi un plateau jouable dans le navigateur :

```
http://127.0.0.1:8000/            # page du plateau (locale)
https://votre-domaine/              # idem derrière Caddy (déploiement public)
```

La page permet de créer une partie (contre le moteur ou partie libre, avec
les noms des joueurs), de la rejoindre par son identifiant — ou de la regarder
en **mode spectateur** (case à cocher) —, de consulter les parties analysées
(mode **Analyse**), les échiquiers d'analyse dry-run (mode **Dry-run**) et les
puzzles publiés (mode **Puzzles**), de jouer à la souris (coups légaux
surlignés, promotion gérée), de suivre les coups en temps réel
(rafraîchissement automatique) et de naviguer dans l'historique coup par coup
— y compris jusqu'à la **position initiale** (avant le 1er coup). Un bouton
**Abandonner la partie** est proposé aux joueurs (jamais aux spectateurs), et
toute fin de partie (abandon, mat, drapeau) **nomme le vainqueur**. Si un
`analyze_game` est lancé pendant qu'une partie ou un puzzle est en cours, une
alerte **« Triche détectée »** s'affiche sous l'échiquier. Les parties
sont **partagées avec les outils MCP** : agents et humains jouent dans les
mêmes parties, chacun son camp.

Si un `LICHESS_MCP_AUTH_TOKEN` est configuré, l'API web l'exige aussi ; la page
demande le token une fois et le mémorise localement.

### Exemple de connexion (client MCP stdio)

```json
{
  "mcpServers": {
    "lichess": {
      "command": "python",
      "args": ["/chemin/vers/lichess-mcp/server.py"]
    }
  }
}
```

## Ajouter des puzzles

`puzzles.json` contient **539 puzzles** : 222 mats en 1 et 317 mats en 2,
validés (position strictement légale, ligne légale, mat final authentique,
« mat en N exactement »). La base intègre des puzzles issus de la
[Lichess Puzzle Database](https://database.lichess.org/#puzzles) (domaine
public) ; les positions illégales de l'ancienne base ont été retirées.
Format :

```json
{
  "PuzzleId": "mate2_001",
  "FEN": "...",
  "Moves": "e2e4 e7e5 ...",   // ligne UCI complète (2N-1 demi-coups)
  "MateIn": 2,
  "Theme": "mateIn2"
}
```

Pour enrichir la base, importez des puzzles au format
[Lichess Puzzle Database](https://database.lichess.org/#puzzles) (champ
`Themes` = `mateIn1`/`mateIn2`) en conservant ce format — seules les
positions strictement légales sont acceptées (vérifiées par
`tests/test_puzzles.py`).
