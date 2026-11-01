# Monkora — paper trading multi-agents

Application de **paper trading uniquement** (aucun argent réel, aucune route vers un courtier) pilotée par **11 agents**, un par skill du dépôt
[Jakeschincariol/day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill) (MIT, commit de référence `3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd`).
Interface web responsive + application de bureau (Windows, macOS 11+), marchés en temps réel via **BiQuote**, résumés IA optionnels via **OpenRouter**.

> **Avertissement.** Outil de simulation et d'étude. Aucune performance passée ou simulée ne garantit un résultat. Ce n'est pas un conseil en investissement.

## Ce qui est vérifié, et ce qui ne l'est pas

| Élément | État | Comment |
|---|---|---|
| Backend (agents, orchestrateur, moteur papier, API) | **Testé** | 95 tests `pytest` (hors tests en direct) |
| Interface web, 15 pages | **Testée** | 27 tests Vitest + 17 tests e2e Playwright (Chromium, bureau, mobile 390 px, tablette 820 px) |
| Application Electron (Linux) | **Lancée et inspectée** | Electron 22.3.27 sous écran virtuel : backend démarré, interface chargée, jeton récupéré |
| Backend embarqué PyInstaller | **Construit et testé sur Linux** | bundle démarré, 11 skills chargés, API servie |
| Installateur **Windows** (NSIS) | **Non construit, non testé** | scripts prêts (`scripts/build-desktop.sh win`) ; à lancer sur Windows |
| Installateur **macOS 11+ Intel** (DMG) | **Non construit, non testé** | scripts prêts (`scripts/build-desktop.sh mac`) ; à lancer sur un Mac |
| **BiQuote en direct** | **Non vérifié** | le réseau de l'environnement de développement bloque biquote.io (HTTP 403) ; connecteur testé avec un faux hub SignalR. `pytest -m live` à lancer chez vous |
| **OpenRouter en direct** | **Non vérifié** | pas de clé dans l'environnement ; client testé avec un transport simulé |
| Yahoo / SEC / House Clerk en direct | **Non vérifié** | idem : testés sur transports simulés et fixtures du dépôt source |

Les tests e2e utilisent un serveur de **test** à données synthétiques (`backend/tests/e2e_server.py`), jamais présenté comme du marché.

## Démarrage rapide (web)

```bash
cp .env.example .env                     # renseignez OPENROUTER_API_KEY (optionnel), SEC_USER_AGENT (optionnel)
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.lock.txt
cd ../frontend && npm ci && npm run build
cd ../backend && .venv/bin/python -m app.main        # http://127.0.0.1:8787
```

Sur la machine qui héberge le backend, l'interface récupère seule son jeton (`/api/session`, boucle locale uniquement, refusé derrière un proxy).
Depuis un autre appareil, saisissez `API_TOKEN` (défini dans `.env`, ou lisez `backend/data/` si généré).

Développement : `cd frontend && npm run dev` (proxy vers le backend, port 5173).

Premiers pas : **Portefeuilles → Nouveau portefeuille**, puis laissez le pipeline tourner (page Vue d'ensemble). Sans portefeuille, aucun agent ne peut prendre de position.

## Architecture

```
frontend/   React 18 + TypeScript + Vite (partagé web et bureau)
backend/    FastAPI + SQLAlchemy/SQLite, WebSocket, orchestrateur, moteur papier
  app/vendor/day_trader_skill/   skills d'origine, NON modifiés
  app/agents/                    un module par agent + adapt.py (adaptations)
  app/market/                    connecteur BiQuote (SignalR), cache de bougies, fraîcheur
  app/paper/engine.py            moteur d'ordres papier
  app/orchestrator.py            pipeline, verrous, délais, audit
desktop/    Electron 22.3.27 : lance le backend embarqué et affiche l'interface
```

### Chaîne de décision (aucun agent ne peut la contourner)

`Entry (signal) → News → Exit (stop/cible) → TimeMachine → Budget (taille) → Risk (PASS/BLOCK) → Broker → moteur papier`

- Une **proposition** est enregistrée pour chaque signal (exécutée, bloquée ou refusée), avec tout son contexte.
- **Exécution automatique** : conformément à la demande de l'utilisateur, une proposition validée par Risk est exécutée **immédiatement, sans attendre d'accord** (`auto_execute`, activé par défaut, réglable). Désactivé, chaque proposition attend une approbation (page Ordres). Dans les deux cas, Risk reste obligatoire et l'empreinte de l'ordre contrôlé doit être identique à celle envoyée.
- **Risk** = règles du skill + règles backend listées séparément dans chaque verdict : interrupteur d'arrêt, portefeuille actif, données fraîches, spread, positions max, pas de pyramidage, perte journalière réalisée, exposition brute, âge du signal, seuil TimeMachine, drapeaux News.
- L'orchestrateur applique un verrou par (agent, instrument, portefeuille), des délais, des nouvelles tentatives, un historique complet (`AgentRun`), la désactivation par agent et un dédoublonnage des signaux.

### Adaptation des skills (actions américaines → tous marchés)

Les skills raisonnent en actions entières, USD, séances NYSE. `adapt.py` transforme sans toucher au skill : **lot virtuel** (prix virtuel ≥ 300, quantité virtuelle = réelle / lot), conversion USD, **séances continues** (fenêtre de 6 h 30 ancrée sur une heure UTC, par défaut 07:00, cartographiée sur 9 h 30–16 h). Détail agent par agent et écarts : [`docs/AGENTS.md`](docs/AGENTS.md).

### Moteur papier

Ordres au marché, limite, stop, stop-limite, bracket ; bid/ask, spread, commissions, glissement ; levier ; persistance SQLite ; un journal (`ledger`) permet de reconstituer l'état (`/api/portfolios/{id}/replay-check`). Un ordre n'est exécuté que sur une cotation **fraîche** (`max_quote_age_s`).

### Données de marché (BiQuote)

Hub `https://biquote.io/hubs/tick` (`Subscribe` / `ReceiveTick`) : connexion persistante, reconnexion automatique, détection de péremption, normalisation, interface `MarketDataProvider`. Chaque cotation est « en direct », « périmée » ou « marché fermé » ; une donnée non fraîche est signalée et **jamais estimée**.

### IA (OpenRouter)

Clé **côté serveur uniquement** (jamais renvoyée au navigateur, jamais journalisée — vérifié par test). Modèle par défaut `minimax/minimax-m3.1-flash`, modèle par agent, test de connexion, limites journalières de coût et de jetons, suivi de latence et d'erreurs. **Les calculs déterministes n'utilisent jamais l'IA** ; elle ne résume que des faits déjà calculés, et l'application fonctionne sans.

## Tests

```bash
cd backend && .venv/bin/python -m pytest                 # 95 tests, sans réseau
cd backend && .venv/bin/python -m pytest -m live         # BiQuote / OpenRouter réels (réseau et clé requis)
cd frontend && npm test && npm run typecheck             # 27 tests Vitest
./scripts/e2e.sh                                         # 17 tests Playwright (build du frontend requis)
cd desktop && npm test                                   # lanceur Electron, backend réel et embarqué
```

## Application de bureau

- **Electron 22.3.27** : dernière branche d'Electron compatible macOS 10.13+ (la 23 exige macOS 10.15+), donc macOS 11 Big Sur. Version épinglée dans `desktop/package.json` ; compatibilité déduite de la documentation d'Electron, **non testée sur macOS 11**.
- Construire sur la plateforme cible : `./scripts/build-desktop.sh win` ou `mac` (PyInstaller et Electron ne compilent pas en croisé). macOS : cible x86_64 (Intel) ; non signé ni notarisé (Gatekeeper demandera une autorisation manuelle).
- Les données sont dans le dossier utilisateur de l'application ; le backend écoute uniquement sur `127.0.0.1`, port libre aléatoire, jeton aléatoire par lancement.

## Limites connues

- **Aucune migration de base** : après une mise à jour qui change le schéma, supprimez la base locale (`backend/data/`).
- Skills conçus pour des actions longues avec stop : les ventes à découvert ne sont pas gérées (le Broker n'ouvre que des achats).
- Broker et Analyst sont **partiellement** implémentés (voir `docs/AGENTS.md`) : pas de courtier externe ; Analyst sans SEC (actions uniquement) passe en mode « contexte de marché ».
- TimeMachine exige ≥ 20 séances complètes d'historique ; sinon « données insuffisantes ».
- Pelosi : lecture des PDF via `pdftotext` (poppler) à installer ; purement informatif, ne génère jamais d'ordre.
- SEC EDGAR exige un User-Agent avec nom et e-mail (Réglages).
- Les graphiques n'affichent la bougie courante en direct que si la cotation est fraîche.
- Installateurs Windows et macOS non vérifiés (voir tableau ci-dessus).

## Licence

MIT. Les skills vendorisés restent © Jake Schincariol (MIT), voir `backend/app/vendor/day_trader_skill/LICENSE` et `VENDOR.md`.

## Mises à jour et releases

- À chaque ouverture, l'application compare sa version à la dernière release de `E10e04/Trading-Desk`. Si elles diffèrent, une fenêtre propose la mise à jour ; « Plus tard » ajoute une icône en bas de la barre latérale (et un panneau Réglages › Apparence et sources › Mises à jour).
- Windows : l'installateur se lance par-dessus l'ancienne version, sans désinstaller. macOS : le DMG s'ouvre, glissez l'application dans Applications. Installateurs non signés ; SHA-256 vérifié avant lancement.
- Publier : Actions › « Release desktop apps » › Run workflow avec `version` (ex. 0.2.0). Le workflow injecte la version, construit les installateurs, crée le tag `vX.Y.Z` et la release.
- BiQuote : SignalR `https://biquote.io/hubs/tick`, `Subscribe(symbols[])`, événement `ReceiveTick`, sans clé API.
- IA : clé OpenRouter et modèle (défaut `minimax/minimax-m3.1-flash`) dans Réglages › Intelligence artificielle. Aucun agent actuel n'utilise la recherche web du LLM ; si elle était ajoutée, OpenRouter la facture (crédits requis) même avec un modèle `:free`.

## Sources de cotations

- **BiQuote** : Forex, métaux, indices, actions (SignalR + REST). Ses cryptos sont des CFD d'un courtier MetaTrader 5.
- **Kraken** (par défaut pour les cryptos) : prix du marché réel en dollar (BTC/USD…), flux WebSocket public `wss://ws.kraken.com/v2`, sans compte ni clé, gratuit. Les 40 paires les plus échangées. Un seul abonnement groupé ; REST limité à 1 appel / 1,1 s ; reconnexions espacées de 5 s au moins (limite Kraken : ~150 connexions / 10 min / IP). Si Kraken est injoignable, les cryptos reviennent à BiQuote. Réglage : Réglages › Données › Source des cryptos (effectif au redémarrage).
- La source de chaque cotation est affichée dans la légende du graphique. Diagnostic : Réglages › Données › Tester la connexion.

## Interface et alertes

- Thème sombre `#1e1e1e` (cartes `#262626`) et thème clair ; barre latérale rétractable (bouton en haut du menu, état conservé ; réduite automatiquement sous 960 px).
- Tableau de bord : cartes de marché avec mini-courbes, graphique, carte portefeuille papier, marchés par classe, plus fortes hausses, dernières exécutions.
- Alerte sur tous les actifs : activée par défaut ; notification quand un instrument varie d'au moins 3 % depuis l'ouverture du jour (une fois par jour et par sens). Réglages › Surveillance et alertes.
- Notifications du bureau : chaque alerte et chaque ordre apparaît dans les notifications du système quand la fenêtre est réduite, masquée ou en arrière-plan (application de bureau : Windows et macOS ; navigateur : onglet en arrière-plan, avec autorisation). Fermer la fenêtre de l'application de bureau la garde active dans la zone de notification (icône : Ouvrir / Quitter). Réglages › Notifications du bureau (interrupteur et bouton « Tester »). Non testé sur de vrais Windows et macOS.
- Le modèle IA choisi dans Réglages › IA s'applique à tous les agents IA (News, Analyste) : les modèles propres à un agent sont effacés au changement, et l'agent affiche « Modèle global ».
- Monkora (ex-Trading Desk) : le dossier de données existant est réutilisé pour ne rien perdre.
- Ventes à découvert (paper) : trois réglages dans Réglages › Exécution (interrupteur global, un interrupteur par portefeuille, sens des signaux Achats / Ventes / Les deux), tous désactivés par défaut. Le signal est la cassure à la baisse de la fourchette d'ouverture, mêmes horaires et même calendrier que les achats. Les skills d'origine (achat seulement) restent intacts : les agents leur présentent des prix en miroir. Non validé sur données réelles ; le TimeMachine rejoue la cassure en miroir.
- Liste de surveillance : un instrument peut avoir plusieurs raisons ; chaque ajout est une ligne à part, avec sa propre notification, et se retire seul. Les anciennes listes sont migrées automatiquement au démarrage.
- Notifications et appels récents des modèles IA : pages de 50, avec navigation 1, 2, 3… en bas du tableau.
- Réglages › Surveillance et alertes : grille « bento » (liste de surveillance à droite, alerte globale et alertes de prix à gauche, mêmes hauteurs).
- Logo : `frontend/public/logo.png` ; icônes de bureau : `desktop/build/icon.ico` et `icon.png`.
