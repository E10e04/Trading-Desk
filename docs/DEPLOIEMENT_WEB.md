# Déployer Monkora sur le web (Northflank, plan gratuit)

Monkora est une application qui tourne en continu (flux de prix BiQuote, agents, WebSocket, base SQLite). Elle ne peut pas tourner sur des
fonctions serverless comme Vercel. Le backend sert déjà l'interface : **un seul service** suffit, avec un volume pour la base.

> Paper trading uniquement. Aucun ordre réel n'est jamais envoyé.

## Ce qu'il faut
- Un compte Northflank (plan gratuit : services toujours actifs, sans mise en veille).
- Ce dépôt GitHub relié à Northflank.
- Un jeton d'accès de 24 caractères minimum, à générer : `openssl rand -hex 24` (ou n'importe quel mot de passe long).

## Étapes
1. **Créer un projet** Northflank, puis **Add new service → Combined service** (build + déploiement).
2. **Source** : le dépôt GitHub, branche `main`. **Build** : Dockerfile à la racine (`/Dockerfile`), contexte `/`.
3. **Port** : `8080`, protocole **HTTP**, public (Northflank fournit l'adresse HTTPS `https://….code.run` et le certificat).
4. **Variables d'environnement** :
   - `API_TOKEN` = ton jeton (obligatoire : sans lui le serveur refuse de démarrer),
   - `CORS_ORIGINS` = l'adresse HTTPS du service (facultatif, même origine par défaut),
   - `OPENROUTER_API_KEY` = ta clé (facultatif : sans elle, pas de résumés IA ; elle peut aussi être saisie dans Réglages),
   - les autres variables du `.env.example` si besoin.
5. **Volume persistant** monté sur `/data` (même petit, 1 Go suffit). C'est là que vivent la base SQLite, les portefeuilles, les ordres et
   l'historique 5 minutes qui alimente le TimeMachine. **Sans volume, tout est perdu à chaque redémarrage.**
6. **Health check** : HTTP, chemin `/api/health`, port 8080.
7. **Instances** : une seule (ne pas faire de réplicas : le flux et les agents ne doivent tourner qu'une fois).
8. Ouvre l'adresse du service : l'écran demande le jeton → colle `API_TOKEN`.

## À vérifier
- Que le plan gratuit permet bien un volume persistant et le trafic WebSocket (`/ws`) : à contrôler dans la console Northflank.
- Les journaux du service doivent montrer « Monkora … démarré — paper trading uniquement ».
- L'indicateur de flux en haut de l'interface passe à « en direct » après quelques secondes.

## Sécurité
- Le jeton est la seule protection : garde-le secret, ne le mets pas dans le dépôt.
- Le serveur refuse de démarrer sur le réseau sans `API_TOKEN` d'au moins 24 caractères.
- L'accès automatique du mode local (`/api/session`) est désactivé derrière un proxy.

## Autres hébergeurs
Le même `Dockerfile` fonctionne sur tout hébergeur de conteneurs (Fly.io, Railway, Render payant, un VPS avec `docker run -p 8080:8080
-v monkora:/data -e API_TOKEN=… image`). Vercel ne convient que pour un frontend seul.
