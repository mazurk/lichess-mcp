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
  meilleur coup à chaque erreur.
- **Règles complètes** gérées par [python-chess](https://github.com/niklasf/python-chess) :
  promotion, prise en passant, roc, pat, règle des 50 coups, répétition triple.
- **Puzzles** au format Lichess : mats en un coup à résoudre via l'agent.
- **Interface web** : plateau interactif dans le navigateur
  (`http://localhost:8000/`) — jouez à la souris, rejoignez une partie par son
  identifiant, ou regardez en **mode spectateur**.
- **Historique** : annulation d'un coup (+ réponse du moteur), abandon par camp, statut.

## Outils MCP exposés

| Outil | Rôle |
|---|---|
| `new_game(white, black, level, engine_color)` | Démarre une partie : noms des deux joueurs ; `engine_color` = camp du moteur (`None` = partie libre) |
| `new_analysis_board(fen)` | Échiquier d'analyse (dry-run) : testez des coups des deux couleurs depuis un FEN |
| `get_board(game_id)` | Échiquier ASCII, FEN, trait, noms des joueurs |
| `legal_moves(game_id)` | Coups légaux au format SAN |
| `play_move(game_id, move, color)` | Joue un coup (SAN ou UCI) pour le camp `color` (défaut : camp au trait) ; refusé pour le camp du moteur |
| `wait_for_turn(game_id, color, timeout_seconds)` | Long-poll : attend que ce soit le tour du camp `color` — jouer en temps réel sans notification |
| `undo(game_id)` | Annule le dernier coup (+ réponse du moteur s'il vient de jouer) |
| `resign(game_id, color)` | Abandon pour le camp indiqué (défaut : camp au trait) |
| `game_status(game_id)` | Statut : joueurs, trait, fin éventuelle |
| `next_puzzle()` | Tire un puzzle Lichess |
| `try_puzzle_move(puzzle_id, move)` | Propose une solution de puzzle |
| `analyze_game(game_id, depth)` | Analyse Stockfish d'une partie ou d'un échiquier d'analyse → PGN annoté + coups critiques |
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
en **mode spectateur** (case à cocher) —, de jouer à la souris (coups légaux
surlignés, promotion gérée), de suivre les coups en temps réel
(rafraîchissement automatique) et de voir l'historique. Les parties sont
**partagées avec les outils MCP** : agents et humains jouent dans les mêmes
parties, chacun son camp.

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

`puzzles.json` contient quelques mats en un coup de démonstration. Vous pouvez
le remplacer par un extrait de la base officielle
[Lichess Puzzle Database](https://database.lichess.org/#puzzles) (champ
`Moves` : coups UCI alternés solution/réponse, le premier coup est toujours
celui à trouver).

## Sémantique des champs (outils MCP)

Les réponses des outils MCP sont **neutres** — aucune notion de « votre » ou
d'« agent » :

- `white_name` / `black_name` : nom du joueur des blancs / des noirs ;
- `turn` : camp au trait ("white"/"black") ;
- `engine_color` : camp joué par le moteur (`null` en partie libre) ;
- `kind` : "game" (partie) ou "analysis" (échiquier d'analyse).

`play_move(color=...)` joue pour le camp indiqué (défaut : camp au trait) ;
le camp du moteur est refusé. N'importe qui connaissant l'identifiant de la
partie peut jouer un camp — deux agents, deux humains, ou un mélange.

## Analyse des parties

Deux outils d'analyse (Stockfish requis : installé dans l'image Docker, ou
`apt install stockfish`, ou chemin via `LICHESS_MCP_STOCKFISH`) :

- `analyze_game(game_id, depth)` — analyse la partie demandée et retourne un
  **PGN annoté compatible Lichess** (commentaires `[%eval ...]`, NAGs `?`,
  `??`, meilleur coup suggéré) plus un résumé (nombre d'erreurs par camp,
  liste des coups critiques) ;
- `analyze_pgn(pgn, depth)` — même analyse pour un PGN importé, utile pour
  les parties jouées sur lichess.org sans avoir à les rejouer.

## Limites

- Partie locale uniquement ; pour jouer **en ligne sur lichess.org**, il
  faudrait ajouter l'[API Lichess](https://lichess.org/api) et un token
  personnel (non requis ici).
- Le moteur intégré reste modeste ; installez Stockfish pour un jeu sérieux.
