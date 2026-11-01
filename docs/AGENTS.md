# Les 11 agents

Généré depuis le code (`scripts/gen_agents_doc.py`). Source des skills : https://github.com/Jakeschincariol/day-trader-skill @ `3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd` (MIT).
Les skills sont copiés **sans modification** dans `backend/app/vendor/day_trader_skill/` ; chaque agent les appelle et ajoute uniquement l'adaptation (voir « Écarts »).

| Agent | Skill | Implémentation | IA |
|---|---|---|---|
| Agent Scanner | `trader-scanner` | complète | non |
| Agent News | `trader-news` | complète | optionnelle (résumé) |
| Agent Entry | `trader-entry` | complète | non |
| Agent Exit | `trader-exit` | complète | non |
| Agent TimeMachine | `trader-timemachine` | complète | non |
| Agent Budget | `trader-budget` | complète | non |
| Agent Risk | `trader-risk` | complète | non |
| Agent Broker | `trader-broker` | **partielle** | non |
| Agent Alerts | `trader-alerts` | complète | non |
| Agent Analyst | `trader-analyst` | **partielle** | optionnelle (résumé) |
| Agent Pelosi Disclosures | `trader-pelosi` | complète | non |

**Pas d'IA dans les calculs** : prix, quantités, risque, stops et cibles sont déterministes. L'IA ne rédige que des résumés de faits déjà calculés.

## Agent Scanner (`scanner`)

**Mission.** Repérer les instruments qui répondent à une règle de setup (cassure, drapeau, survente...) et alimenter la liste de surveillance.

**Skill source.** `trader-scanner` — implémentation **complète**.

**Entrées.**

- symbols (optionnel)

**Sorties.**

- hits[] (symbol, setups, raisons, prix)
- counts
- watchlist

**Dépendances.**

- BiQuote REST (bougies 5m et 1d)
- flux de cotations

**Paramètres par défaut.**

- `modes` = `['daily', 'orb']` — daily (clôtures), orb (fourchette d'ouverture)
- `or_minutes` = `15` — durée de la fourchette d'ouverture (multiple de 5)
- `max_spread_bps` = `25.0` — ignore les instruments dont le spread dépasse ce nombre de points de base
- `max_instruments` = `300`
- `concurrency` = `8`
- `asset_classes` = `[]` — restreindre à forex, metal, energy, crypto, index, equity (vide = toutes)
- `no_entries_after` = `15:30`
- `session_anchor_utc` = `07:00`
- `watchlist_ttl_h` = `12`

**Écarts avec le skill d'origine et limites.**

- Univers = instruments BiQuote cotant à cet instant (forex, métaux, énergie, crypto, indices, CFD actions) au lieu des ~5 900 actions US du skill.
- BiQuote ne fournit aucun volume réel (CFD) : le filtre de liquidité en volume du skill est remplacé par un filtre de spread et de fraîcheur.
- Marchés continus : la « séance » d'ouverture est une fenêtre de 6h30 démarrant à l'ancrage UTC configuré ; le calendrier US des skills s'y applique (pas de séances le week-end).

## Agent News (`news`)

**Mission.** Lire les actualités, dépôts officiels et événements macro qui touchent les instruments suivis, et ne remonter que le nouveau.

**Skill source.** `trader-news` — implémentation **complète**.

**Entrées.**

- symbols

**Sorties.**

- items[]
- per_symbol[]
- risk_flags[]
- sources[] (statut de chaque source)
- summary_ai

**Dépendances.**

- Yahoo Finance RSS (actions)
- SEC EDGAR (actions, SEC_USER_AGENT)
- BiQuote /api/news et /api/calendar
- OpenRouter (résumé optionnel)

**Paramètres par défaut.**

- `hours` = `12` — fenêtre de recherche en heures
- `calendar_hours_ahead` = `6` — événements macro à surveiller dans les N prochaines heures
- `use_llm` = `True` — résumé IA (faits / pistes / incertitudes) si une clé OpenRouter est configurée
- `mark_seen` = `True` — mémoriser ce qui a été montré
- `max_items` = `40`

**Écarts avec le skill d'origine et limites.**

- Sources réelles uniquement : Yahoo Finance + SEC EDGAR 8-K pour les actions (SEC exige un SEC_USER_AGENT), flux d'actualités et calendrier économique BiQuote pour le reste.
- La variation du jour (MOVING) est calculée depuis la dernière clôture journalière BiQuote, avec un seuil propre à chaque classe d'actifs (3 % pour les actions, 0,6 % pour le forex...).
- Un titre est une piste, pas un fait : seuls les dépôts officiels et le calendrier sont présentés comme des faits.

## Agent Entry (`entry`)

**Mission.** Détecter le signal d'entrée (cassure de la fourchette d'ouverture ou d'un niveau) sur bougies de 5 minutes clôturées.

**Skill source.** `trader-entry` — implémentation **complète**.

**Entrées.**

- symbol
- setup (orb|level)
- level (prix réel, si setup=level)
- side (buy|sell : sell = cassure à la baisse, vente à découvert)

**Sorties.**

- status (triggered|waiting|expired|no-data)
- trigger{price, closes_at, minutes_since, pct_over}
- opening_range
- level
- reason

**Dépendances.**

- bougies 5 minutes (BiQuote)

**Paramètres par défaut.**

- `setup` = `orb` — orb (fourchette d'ouverture) ou level (clôture au-dessus d'un niveau)
- `or_minutes` = `15` — durée de la fourchette d'ouverture (multiple de 5)
- `no_entries_before` = `09:45` — pas d'entrée avant cette heure (séance mappée, HH:MM)
- `no_entries_after` = `15:30` — pas d'entrée après cette heure (séance mappée, HH:MM)
- `session_anchor_utc` = `07:00`
- `bars_limit` = `320`

**Écarts avec le skill d'origine et limites.**

- Barres 5 minutes construites depuis le flux BiQuote (ou son historique) : pas de volume réel (CFD), donc le filtre « volume minimal » du skill est ignoré.
- Marchés continus : la fourchette d'ouverture démarre à l'ancrage UTC configuré (séance mappée 6h30) ; fenêtre d'entrée du skill appliquée à cette séance mappée.
- Vente à découvert (sens `sell`) : cassure à la BAISSE de la fourchette, évaluée sur des prix en miroir avec le même skill ; seulement si l'interrupteur global et celui du portefeuille sont activés.
- Pas de mode `watch` interactif du skill : la surveillance est assurée par l'orchestrateur (à chaque clôture de bougie 5 min).

## Agent Exit (`exit`)

**Mission.** Définir stop, cible et heure de sortie forcée (flat-by) d'un trade, et clôturer les positions arrivées à l'heure limite.

**Skill source.** `trader-exit` — implémentation **complète**.

**Entrées.**

- symbol
- entry (prix réel)
- opening_range{high,low}
- entry_time (UTC)
- stop
- target

**Sorties.**

- plan{entry, stop, target, risk_per_unit, reward_risk, flat_by, flat_by_utc}
- warnings

**Dépendances.**

- Agent Entry (signal)
- bougies 5 minutes (stop ATR)

**Paramètres par défaut.**

- `stop` = `or-mid` — or-mid, or-low, price:X, pct:X ou atr:K
- `target` = `2R` — 2R, 1.5R ou price:X
- `flat_by` = `15:55` — heure de sortie forcée (séance mappée HH:MM)
- `min_reward_risk` = `1.0`
- `session_anchor_utc` = `07:00`
- `monitor_enabled` = `True` — clôturer automatiquement à l'heure flat-by

**Écarts avec le skill d'origine et limites.**

- Le skill ne planifie que des achats : la vente à découvert (réglages : interrupteur global + portefeuille) est calculée sur des prix « en miroir » (prix de référence × 2 − prix), puis reconvertie ; le skill reste intact. Stop arrondi vers le haut dans ce sens.
- Calculs faits en prix « virtuels » (centime) puis reconvertis : stop arrondi vers le bas, cible à la précision de l'instrument.
- Le stop/cible sont exécutés par le moteur papier (stop = déclenchement puis marché, donc peut glisser) — pas de garantie de prix.

## Agent TimeMachine (`timemachine`)

**Mission.** Rejouer 1 000+ futurs plausibles du trade proposé (cible avant stop ?) à partir de l'historique propre de l'instrument.

**Skill source.** `trader-timemachine` — implémentation **complète**.

**Entrées.**

- plan (symbol, entry, stop, target, entry_bar)

**Sorties.**

- p_target, p_stop, p_neither, avg_R
- edge (écart vs seuil de rentabilité)
- verdict
- history{triggered, target, stop}

**Dépendances.**

- Agent Exit (plan)
- historique 5 minutes (≥ 20 séances)

**Paramètres par défaut.**

- `replays` = `5000` — nombre de futurs simulés (1 à 1 000 000)
- `seed` = `19` — graine : mêmes chiffres pour les mêmes données
- `window` = `2` — tolérance (en barres) du rééchantillonnage horaire
- `bars_limit` = `3200`
- `session_anchor_utc` = `07:00`
- `min_edge` = `None` — écart minimal (cible vs seuil de rentabilité) sous lequel le trade est bloqué ; vide = information seulement

**Écarts avec le skill d'origine et limites.**

- Nécessite ≥ 20 séances COMPLÈTES de 78 bougies de 5 minutes : si l'historique BiQuote est trop court ou troué, l'agent répond « données insuffisantes » (jamais de chiffre inventé).
- Séances mappées pour les marchés continus (fenêtre de 6h30 depuis l'ancrage UTC) : un trou de cotation dans la fenêtre invalide la séance.
- Simulation, pas une prédiction : actualités, annonces et gaps ne sont pas modélisés.
- Le rejeu historique ne couvre que le setup `orb` (comme le skill).

## Agent Budget (`budget`)

**Mission.** Dimensionner la position : quantité maximale respectant perte max par trade, taille de position, ordre max et marge disponible.

**Skill source.** `trader-budget` — implémentation **complète**.

**Entrées.**

- plan (Agent Exit)
- portfolio_id

**Sorties.**

- qty (réelle)
- entry/stop/target
- max_loss_usd
- notional_usd
- margin_usd
- bound_by
- limits[]
- refusal

**Dépendances.**

- Agent Exit (plan)
- portefeuille papier (equity, marge libre)
- cotation fraîche

**Paramètres par défaut.**

- `order_type` = `market` — market ou limit (limit : au prix d'entrée du plan)
- `session_anchor_utc` = `07:00`

**Écarts avec le skill d'origine et limites.**

- Mode « un plan = un ordre » (run_plan) du skill ; le mode d'allocation de poids (`--weights`) n'est pas utilisé.
- Pouvoir d'achat = equity x levier du portefeuille papier (le skill raisonne en cash d'un compte actions sans levier).
- Prix et quantités convertis via le lot virtuel ; la perte max est revérifiée en prix réels et la quantité réduite d'un pas si besoin.
- Fractions : la quantité réelle est un multiple du pas de l'instrument (forex 1 000, métaux 0,1, crypto 0,001...).

## Agent Risk (`risk`)

**Mission.** Valider (PASS) ou refuser (BLOCK) chaque ordre proposé selon les limites de risque du portefeuille, avant toute exécution.

**Skill source.** `trader-risk` — implémentation **complète**.

**Entrées.**

- sizing (Agent Budget)
- signal (Agent Entry)
- timemachine (optionnel)
- news (optionnel)
- portfolio_id

**Sorties.**

- verdict PASS|BLOCK
- reasons[]
- warnings[]
- max_loss
- reward_risk
- fingerprint
- backend_rules[]
- skill_result

**Dépendances.**

- Agent Budget (ordre dimensionné)
- moteur papier (portefeuille, journal)
- cotation fraîche

**Paramètres par défaut.**

- `max_signal_age_min` = `20` — âge maximal du signal d'entrée (minutes) ; au-delà : BLOCK
- `max_spread_bps` = `None` — spread maximal (points de base) ; vide = valeur du portefeuille
- `max_open_positions` = `None` — nombre max de positions ouvertes ; vide = valeur du portefeuille
- `allow_add_to_position` = `False`
- `session_anchor_utc` = `07:00`
- `block_on_high_news` = `True` — BLOCK si l'agent News a levé une alerte de niveau high sur l'instrument (réglage global news_block_on_halt)

**Écarts avec le skill d'origine et limites.**

- Le skill ne connaît que les tickers d'actions US : l'ordre est présenté sous un code d'action stable et en « actions virtuelles » (voir docs/AGENTS.md). Les règles propres aux CFD sont des règles backend ajoutées.
- « Cash » du skill = pouvoir d'achat restant (marge libre x levier) ; « compte » = equity x levier.
- Le journal d'ordres du jour est reconstruit depuis le moteur papier (jour = date UTC réelle) au lieu de trader/state/broker.json.
- Les règles ajoutées par le backend ne figurent pas dans le skill d'origine : elles sont listées séparément (`backend_rules`) dans chaque verdict.

## Agent Broker (`broker`)

**Mission.** Transformer une proposition validée par Risk en ordre bracket papier (entrée + stop + cible) et suivre son exécution.

**Skill source.** `trader-broker` — implémentation **partielle**.

**Entrées.**

- proposal (sizing + risk + plan)
- reply (réponse de l'utilisateur, si approbation manuelle)
- mode: submit | flatten

**Sorties.**

- status (executed|working|rejected|pending_approval|refused)
- order
- ticket[]
- decision (auto|user)

**Dépendances.**

- Agent Risk (PASS obligatoire)
- moteur papier

**Paramètres par défaut.**

- `order_type` = `market` — market (par défaut) ou limit

**Écarts avec le skill d'origine et limites.**

- Courtier = moteur papier interne : pas de MCP Alpaca ni de compte externe (le mode live n'existe pas dans cette version).
- Utilisés du skill : empreinte d'ordre (fingerprint), validation du « yes » (parse_yes) avec « oui » accepté en plus. Non repris : fichiers state/broker.json et portfolio.json (remplacés par la base SQLite).
- Mode auto_execute : l'approbation par l'utilisateur est remplacée par une décision de politique journalisée ; les contrôles Risk, fraîcheur et moteur restent appliqués.

## Agent Alerts (`alerts`)

**Mission.** Surveiller les niveaux (stop, cible, seuils, mouvements, heures) et les incidents du système, et notifier une seule fois par règle.

**Skill source.** `trader-alerts` — implémentation **complète**.

**Entrées.**

- (aucune : lit les règles actives)

**Sorties.**

- rings[] (règles déclenchées)
- skipped_stale[]
- errors[]
- operational[]

**Dépendances.**

- flux de cotations
- table alert_rules

**Paramètres par défaut.**

- `session_anchor_utc` = `07:00`
- `operational` = `True` — produire aussi les alertes opérationnelles (flux, IA, interrupteur d'arrêt)
- `stale_throttle_s` = `900` — délai minimal entre deux alertes opérationnelles identiques

**Écarts avec le skill d'origine et limites.**

- Prix fournis par le flux BiQuote (pas de Yahoo/Stooq) ; aucune alerte de prix sur cotation périmée ou marché fermé.
- Les heures des règles « time » et l'ouverture « opening_bell » suivent la séance mappée (marchés continus : 6h30 depuis l'ancrage UTC).
- Notifications dans l'application (cloche + WebSocket) ; pas d'e-mail/SMS/push externe dans cette version.
- Alertes opérationnelles (flux, IA, interrupteur) = ajout de l'application, absentes du skill.

## Agent Analyst (`analyst`)

**Mission.** Expliquer les résultats déclarés d'une société (actions) ou, à défaut, le contexte de marché récent d'un instrument — sans recommandation.

**Skill source.** `trader-analyst` — implémentation **partielle**.

**Entrées.**

- symbol

**Sorties.**

- mode (sec|market_context)
- rows[]
- changes[]
- filings
- market_context
- summary_ai

**Dépendances.**

- SEC EDGAR (HTTPS, actions)
- bougies journalières BiQuote
- OpenRouter (optionnel)

**Paramètres par défaut.**

- `quarters` = `6` — nombre de trimestres affichés
- `use_llm` = `True` — résumé en langage clair par l'IA (si disponible)

**Écarts avec le skill d'origine et limites.**

- Résultats SEC (XBRL) disponibles uniquement pour les actions américaines ayant un CIK ; configuration SEC_USER_AGENT (nom + e-mail) obligatoire.
- Forex, métaux, énergie, crypto, indices : pas de bilans — l'agent ne fournit qu'un contexte de marché calculé sur les bougies journalières (statut « partiel »).
- L'IA (optionnelle) ne fait que reformuler des chiffres déjà calculés ; aucune recommandation.
- Les pièces jointes des communiqués (EX-99) ne sont pas téléchargées : liens uniquement.

## Agent Pelosi Disclosures (`pelosi`)

**Mission.** Informer des nouvelles déclarations de transactions boursières de membres de la Chambre (House Clerk), sans jamais trader dessus.

**Skill source.** `trader-pelosi` — implémentation **complète**.

**Entrées.**

- members (optionnel)
- year (optionnel)

**Sorties.**

- filings[] (nouveaux dépôts)
- trades[] (lignes lues)
- disclaimer

**Dépendances.**

- disclosures-clerk.house.gov (HTTPS)
- pdftotext (optionnel)

**Paramètres par défaut.**

- `members` = `['Pelosi']` — noms recherchés (ex. « Pelosi », « Nancy Pelosi »)
- `year` = `None` — année de l'index (vide = année en cours)
- `read_pdfs` = `True` — télécharger et lire les nouveaux PDF (pdftotext requis)
- `max_pdfs` = `5` — nombre maximal de PDF lus par exécution
- `first_run_days` = `45`
- `mark_seen` = `True` — mémoriser les dépôts vus (une alerte par dépôt)
- `notify` = `True`

**Écarts avec le skill d'origine et limites.**

- Chambre des représentants uniquement (le Sénat exige l'acceptation d'un accord sur efdsearch.senate.gov : non automatisé).
- Lecture des PDF : nécessite l'outil `pdftotext` (poppler) sur la machine ; sans lui, l'agent liste les dépôts avec le lien du PDF (statut « partiel »).
- Données différées et en tranches ; les titres sont des actions US : un rapprochement avec un instrument BiQuote n'est indiqué que si le ticker existe dans le catalogue.
- État « déjà vu » conservé en base (table disclosures_seen) au lieu de trader/state/pelosi.json.
