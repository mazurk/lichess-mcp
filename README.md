# lichess-mcp

Serveur MCP d'échecs **hors ligne**, pensé pour l'agent Vibe de Mistral.
Aucune connexion à lichess.org (et donc aucun token) n'est nécessaire : les
parties se jouent en local contre un moteur intégré, et les puzzles suivent le
format Lichess (FEN + coups UCI).

## Fonctionnalités

- **Jouer contre le moteur** : trois niveaux (`easy`, `medium`, `hard`), aux
  blancs ou aux noirs. Le moteur intégré est un negamax alpha-beta ; si un
  binaire [Stockfish](https://stockfishchess.org/) est installé sur la machine,
  il est utilisé automatiquement.
- **Règles complètes** gérées par [python-chess](https://github.com/niklasf/python-chess) :
  promotion, prise en passant, roc, pat, règle des 50 coups, répétition triple.
- **Puzzles** au format Lichess : mats en un coup à résoudre via l'agent.
- **Historique** : annulation d'un coup (+ réponse du moteur), abandon, statut.

## Outils MCP exposés

| Outil | Rôle |
|---|---|
| `new_game(color, level)` | Démarre une partie (le moteur joue s'il a les blancs) |
| `get_board(game_id)` | Échiquier ASCII, FEN, trait |
| `legal_moves(game_id)` | Coups légaux au format SAN |
| `play_move(game_id, move)` | Joue votre coup (SAN ou UCI), le moteur répond |
| `undo(game_id)` | Annule votre coup et la réponse du moteur |
| `resign(game_id)` | Abandonne |
| `game_status(game_id)` | Statut de la partie |
| `next_puzzle()` | Tire un puzzle Lichess |
| `try_puzzle_move(puzzle_id, move)` | Propose une solution de puzzle |

## Installation

```bash
git clone https://github.com/mazurk/lichess-mcp.git
cd lichess-mcp
python -m venv .venv && source .venv/bin/activate  # Windows : .venv\Scripts\activate
pip install -r requirements.txt
```

## Exécution

Le serveur fonctionne en **stdio** (le client MCP le lance et communique via
l'entrée/sortie standard) :

```bash
python server.py
```

Pour l'exposer en **SSE sur HTTP** (ex. `http://127.0.0.1:8000/mcp`), utile pour
un client qui se connecte par URL :

```python
# dans server.py, remplacer le bloc final par :
mcp.run(transport="http", host="127.0.0.1", port=8000)
```

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

## Limites

- Partie locale uniquement ; pour jouer **en ligne sur lichess.org**, il
  faudrait ajouter l'[API Lichess](https://lichess.org/api) et un token
  personnel (non requis ici).
- Le moteur intégré reste modeste ; installez Stockfish pour un jeu sérieux.
