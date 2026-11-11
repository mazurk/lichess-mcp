# Déploiement du serveur MCP pour un agent cloud (ex. Vibe de Mistral)

Ce guide explique comment rendre le serveur MCP joignable depuis Internet
depuis votre propre machine, de façon sécurisée.

> **Alternative locale** : si vous utilisez un client MCP qui tourne sur la même
> machine (ex. Claude Desktop), aucun de cela n'est nécessaire — lancez
> `python server.py` (stdio) et déclarez-le dans la config du client.

## Architecture

```
Agent cloud (Vibe/Studio) ──HTTPS──▶ Caddy (reverse proxy, Let's Encrypt)
                                          │
                                          ▼ (réseau Docker privé)
                                   lichess-mcp (HTTP, port 8000)
```

Le token bearer bloque tout accès non autorisé ; Caddy fournit le HTTPS.

## 1. Prérequis

- Docker + Docker Compose installés ;
- un **nom de domaine** pointant vers votre machine. Si votre IP est dynamique,
  utilisez un DDNS (DuckDNS, No-IP…) pour obtenir une URL stable ;
- sur votre box : **redirection des ports 80 et 443** vers votre machine.

## 2. Configuration

```bash
cp .env.example .env
# Générez un token fort :
python -c "import secrets; print(secrets.token_urlsafe(32))"
# Mettez-le dans .env : LICHESS_MCP_AUTH_TOKEN=...
```

Copiez le modèle puis éditez-le avec votre domaine :

```bash
cp Caddyfile.example Caddyfile
# remplacez chess.votre-domaine.tld par votre domaine dans Caddyfile
```

`Caddyfile` est ignoré par git : vos `git pull` ne l'écraseront jamais.
Le modèle versionné est `Caddyfile.example`.

## 3. Lancement

```bash
docker compose up -d --build
docker compose logs -f caddy   # vérifier l'obtention du certificat
```

Le serveur MCP est alors joignable sur :

```
https://chess.votre-domaine.tld/mcp
```

## 3 bis. Interface web (plateau navigateur)

Le serveur expose aussi un plateau jouable dans le navigateur à la racine :
`https://chess.votre-domaine.tld/`. L'API web (`/web/api/...`) exige le même
token bearer que le MCP ; la page le demande une fois à la première requête
et le mémorise localement. Les parties créées via le navigateur sont
partagées avec l'agent (mêmes identifiants de partie).

## 4. Tester

```bash
curl -s -H "Authorization: Bearer $LICHESS_MCP_AUTH_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
          "params":{"protocolVersion":"2025-06-18","capabilities":{},
                    "clientInfo":{"name":"curl","version":"0"}}}' \
     https://chess.votre-domaine.tld/mcp
```

Une réponse JSON contenant `serverInfo.lic-hess-mcp` (ou similaire) confirme
que tout fonctionne. Sans l'en-tête d'authentification, vous devez obtenir une
erreur 401.

## 5. Déclarer le connecteur dans le Studio Mistral

1. Studio Mistral → **Connecteurs** → ajouter un **serveur MCP custom** ;
2. URL : `https://chess.votre-domaine.tld/mcp` ;
3. Authentification : en-tête `Authorization: Bearer <votre-token>` ;
4. Une fois connecté, l'agent voit les 9 outils (`new_game`, `play_move`,
   `next_puzzle`…) et peut jouer contre vous.

## 6. Mode HTTP manuel (sans Docker)

```bash
export LICHESS_MCP_TRANSPORT=http
export LICHESS_MCP_HOST=127.0.0.1      # 0.0.0.0 si exposé derrière un proxy
export LICHESS_MCP_PORT=8000
export LICHESS_MCP_AUTH_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
python server.py
```

⚠️ En HTTP sans reverse proxy TLS, le token circule en clair : réservez ce mode
aux tests locaux, ou placez Caddy/nginx devant.

## Variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `LICHESS_MCP_TRANSPORT` | `stdio` | `stdio` ou `http` |
| `LICHESS_MCP_HOST` | `127.0.0.1` | Adresse d'écoute en mode HTTP |
| `LICHESS_MCP_PORT` | `8000` | Port d'écoute en mode HTTP |
| `LICHESS_MCP_AUTH_TOKEN` | *(vide)* | Token bearer requis si défini |

## Sécurité

- Ne publiez jamais votre token (ne committez pas `.env` — il est ignoré par
  git) ;
- Caddy renouvelle le certificat automatiquement ;
- le serveur n'expose que des opérations d'échecs : pas de shell, pas de
  système de fichiers ;
- pour verrouiller davantage, vous pouvez restreindre l'accès par IP source
  dans le Caddyfile si les plages sortantes du Studio sont connues.
