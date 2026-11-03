"""Réglages applicatifs persistés en base (table `settings`)."""
from __future__ import annotations

import threading
from typing import Any

from .config import DEFAULT_MODEL
from .db import session_scope
from .models import Setting

DEFAULTS: dict[str, Any] = {
    # Exécution : le système prend les positions sans attendre de validation (demande explicite de l'utilisateur).
    # Paper trading UNIQUEMENT : aucune route vers un courtier réel n'existe dans cette version.
    "auto_execute": True,
    "pipeline_enabled": True,            # l'orchestrateur déclenche les agents automatiquement
    "kill_switch": False,                # coupe immédiatement toute prise de position automatique
    "allow_short": False,                # interrupteur GLOBAL des ventes à découvert (il faut aussi celui du portefeuille)
    "trade_direction": "long",           # sens des signaux : long (achats) | short (ventes) | both
    "max_quote_age_s": 30,               # une cotation plus vieille ne peut pas servir à exécuter un ordre
    "quote_live_s": 30,
    "quote_hard_stale_s": 300,
    "crypto_source": "kraken",           # kraken : prix du marché réel (Kraken) | biquote : CFD du courtier MT5 ; effectif au prochain démarrage
    "alert_all_assets": True,            # alerte de variation journalière sur TOUS les instruments disponibles
    "alert_all_pct": 3.0,                # seuil (%) de variation depuis l'ouverture du jour
    "scanner_universe": "live",          # live | watchlist | all
    "scanner_max_instruments": 300,
    "llm_model": DEFAULT_MODEL,           # modèle OpenRouter utilisé par défaut par tous les agents (surchargeable par agent)
    "llm_enabled": True,                 # résumés IA optionnels (News/Analyst/Pelosi)
    "llm_daily_cost_limit_usd": 1.0,
    "llm_daily_token_limit": 300000,
    "llm_timeout_s": 45,
    "news_block_on_halt": False,
    "timemachine_required": True,        # la simulation TimeMachine est obligatoire avant tout ordre : sans elle (historique insuffisant), Risk bloque
    "timemachine_min_edge": -0.05,       # écart (part de cibles atteintes en premier − seuil de rentabilité) sous lequel Risk bloque ; -0.05 = on refuse quand la simulation penche nettement vers le stop ; vide = informatif
    "proposal_ttl_min": 15,              # une proposition en attente d'approbation expire après ce délai
    "sec_user_agent": "",
    "equity_snapshot_s": 60,
    "history_yahoo_backfill": True,      # complète l'historique 5 min manquant (TimeMachine) avec Yahoo Finance : source non officielle, désactivable
}
_cache: dict[str, Any] | None = None
_lock = threading.Lock()


def _load() -> dict[str, Any]:
    global _cache
    with _lock:
        if _cache is None:
            data = dict(DEFAULTS)
            with session_scope() as s:
                for row in s.query(Setting).all():
                    data[row.key] = row.value
            _cache = data
        return _cache


def get_all() -> dict[str, Any]:
    return dict(_load())


def get(key: str, default: Any = None) -> Any:
    d = _load()
    return d.get(key, DEFAULTS.get(key, default))


def update(values: dict[str, Any]) -> dict[str, Any]:
    unknown = [k for k in values if k not in DEFAULTS]
    if unknown:
        raise KeyError(f"réglage inconnu: {', '.join(unknown)}")
    for k, v in values.items():
        want = DEFAULTS[k]
        if want is not None and v is not None and not isinstance(v, type(want)) and not (isinstance(want, (int, float)) and isinstance(v, (int, float)) and not isinstance(v, bool)):
            raise TypeError(f"réglage {k}: type attendu {type(want).__name__}")
    with session_scope() as s:
        for k, v in values.items():
            row = s.get(Setting, k)
            if row:
                row.value = v
            else:
                s.add(Setting(key=k, value=v))
    reset_cache()
    return get_all()


def reset_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def trade_directions() -> list[str]:
    """Sens évalués par les agents : achats par défaut ; ventes (à découvert) seulement si l'interrupteur global est activé."""
    mode = str(get("trade_direction") or "long")
    shorts = bool(get("allow_short"))
    if mode == "short":
        return ["sell"] if shorts else []
    if mode == "both":
        return ["buy", "sell"] if shorts else ["buy"]
    return ["buy"]
