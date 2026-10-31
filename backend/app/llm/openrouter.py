"""Client OpenRouter (premier fournisseur IA).

* La clé n'est lue que côté backend (SecretStore) ; elle n'apparaît jamais dans l'API, les logs ni les erreurs.
* Liste des modèles disponibles (catalogue public d'OpenRouter, mis en cache) pour le choix manuel.
* Test de connexion, suivi des erreurs/délais/coûts (table `llm_usage`), limites journalières configurables.
* L'IA ne sert qu'aux résumés/explications : jamais aux calculs financiers ni aux validations critiques.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time
from typing import Any

import httpx
from sqlalchemy import func, select

from .. import settings_store
from ..config import SecretStore, get_settings, mask, redact
from ..db import session_scope
from ..models import LlmUsage, utcnow

log = logging.getLogger(__name__)


class LlmError(RuntimeError):
    pass


class LlmUnavailable(LlmError):
    """Pas de clé, IA désactivée ou limite atteinte : l'agent doit continuer sans IA."""


class OpenRouterClient:
    def __init__(self, secrets: SecretStore | None = None, *, transport: httpx.AsyncBaseTransport | None = None,
                 base_url: str | None = None) -> None:
        self.secrets = secrets or SecretStore()
        self.base_url = (base_url or get_settings().openrouter_base_url).rstrip("/")
        self._transport = transport
        self._models: list[dict] = []
        self._models_at = 0.0
        self.last_error: str | None = None
        self.last_ok_at: dt.datetime | None = None

    # ------------------------------------------------------------------ config
    @property
    def api_key(self) -> str:
        return self.secrets.get("openrouter_api_key")

    @property
    def default_model(self) -> str:
        return (str(settings_store.get("llm_model") or "").strip()) or get_settings().openrouter_default_model

    def key_status(self) -> dict[str, Any]:
        k = self.api_key
        return {"configured": bool(k), "masked": mask(k) if k else "", "source": self.secrets.source("openrouter_api_key"),
                "default_model": self.default_model}

    def set_key(self, key: str) -> None:
        key = key.strip()
        if key and not key.startswith("sk-or-"):
            raise ValueError("une clé OpenRouter commence par « sk-or- »")
        self.secrets.set("openrouter_api_key", key)

    def _client(self, timeout: float) -> httpx.AsyncClient:
        headers = {"Content-Type": "application/json", "HTTP-Referer": "http://localhost", "X-Title": "Trading Desk"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=timeout, transport=self._transport)

    def _clean(self, text: str) -> str:
        return redact(text, [self.api_key])[:300]

    # ------------------------------------------------------------------ modèles & connexion
    async def list_models(self, refresh: bool = False) -> list[dict]:
        if self._models and not refresh and time.monotonic() - self._models_at < 3600:
            return self._models
        try:
            async with self._client(20) as c:
                r = await c.get("/models")
                r.raise_for_status()
                rows = r.json().get("data", [])
        except (httpx.HTTPError, ValueError) as e:
            self.last_error = self._clean(f"catalogue des modèles: {e}")
            if self._models:
                return self._models
            raise LlmError(self.last_error) from e
        out = []
        for m in rows:
            p = m.get("pricing") or {}

            def f(x):
                try:
                    return float(x)
                except (TypeError, ValueError):
                    return None

            out.append({"id": m.get("id"), "name": m.get("name") or m.get("id"), "context_length": m.get("context_length"),
                        "prompt_per_mtok": (f(p.get("prompt")) or 0) * 1e6 if p.get("prompt") is not None else None,
                        "completion_per_mtok": (f(p.get("completion")) or 0) * 1e6 if p.get("completion") is not None else None,
                        "free": f(p.get("prompt")) == 0 and f(p.get("completion")) == 0})
        out = [m for m in out if m["id"]]
        out.sort(key=lambda m: m["id"])
        self._models, self._models_at = out, time.monotonic()
        return out

    async def test_connection(self, model: str | None = None) -> dict[str, Any]:
        """Vérifie la clé (route d'info de clé) puis fait un appel minimal au modèle."""
        res: dict[str, Any] = {"ok": False, "key_configured": bool(self.api_key), "model": model or self.default_model}
        if not self.api_key:
            res["error"] = "aucune clé OpenRouter configurée"
            return res
        t0 = time.monotonic()
        res["steps"] = []
        try:
            async with self._client(20) as c:
                for path in ("/key", "/auth/key"):
                    r = await c.get(path)
                    if r.status_code == 200:
                        d = r.json().get("data", {})
                        res["key_label"] = d.get("label")
                        res["credit_limit"], res["usage_usd"] = d.get("limit"), d.get("usage")
                        res["free_tier"] = d.get("is_free_tier")
                        res["steps"].append("clé acceptée par OpenRouter")
                        break
                    if r.status_code in (401, 403):
                        res["error"] = "clé refusée par OpenRouter (401/403) : vérifiez qu'elle est complète, active et non révoquée"
                        return res
        except httpx.HTTPError as e:
            res["error"] = self._clean(f"OpenRouter injoignable: {type(e).__name__} {e}")
            self.last_error = res["error"]
            return res
        try:
            # Les modèles « raisonnants » (dont MiniMax M3.1) dépensent des jetons à réfléchir avant de répondre :
            # avec un plafond minuscule la réponse sortait vide. On laisse donc de la marge.
            out = await self.chat([{"role": "user", "content": "Réponds uniquement par le mot : ok"}], model=model, agent_id="test",
                                  max_tokens=400, force=True, allow_reasoning_text=True)
            res.update(ok=True, reply=out["text"][:60], latency_ms=out["latency_ms"], cost_usd=out.get("cost_usd"))
            res["steps"].append(f"le modèle {res['model']} a répondu")
        except LlmError as e:
            res["error"] = str(e)
        res["total_ms"] = int((time.monotonic() - t0) * 1000)
        return res

    # ------------------------------------------------------------------ limites
    def usage_today(self) -> dict[str, Any]:
        start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        with session_scope() as s:
            row = s.execute(select(func.coalesce(func.sum(LlmUsage.prompt_tokens + LlmUsage.completion_tokens), 0),
                                   func.coalesce(func.sum(LlmUsage.cost_usd), 0.0), func.count(LlmUsage.id),
                                   func.coalesce(func.sum(func.iif(LlmUsage.ok, 0, 1)), 0))
                            .where(LlmUsage.ts >= start)).one()
        return {"tokens": int(row[0]), "cost_usd": float(row[1]), "calls": int(row[2]), "errors": int(row[3]),
                "token_limit": settings_store.get("llm_daily_token_limit"), "cost_limit_usd": settings_store.get("llm_daily_cost_limit_usd")}

    def _check_limits(self) -> None:
        u = self.usage_today()
        if u["token_limit"] and u["tokens"] >= u["token_limit"]:
            raise LlmUnavailable(f"limite journalière de jetons atteinte ({u['tokens']}/{u['token_limit']})")
        if u["cost_limit_usd"] and u["cost_usd"] >= u["cost_limit_usd"]:
            raise LlmUnavailable(f"limite journalière de coût atteinte ({u['cost_usd']:.3f}/{u['cost_limit_usd']} USD)")

    def available(self) -> tuple[bool, str]:
        if not settings_store.get("llm_enabled"):
            return False, "IA désactivée dans les réglages"
        if not self.api_key:
            return False, "aucune clé OpenRouter configurée"
        try:
            self._check_limits()
        except LlmUnavailable as e:
            return False, str(e)
        return True, ""

    # ------------------------------------------------------------------ appel
    async def chat(self, messages: list[dict], *, model: str | None = None, agent_id: str | None = None, max_tokens: int = 600,
                   temperature: float = 0.2, force: bool = False, timeout: float | None = None,
                   allow_reasoning_text: bool = False) -> dict[str, Any]:
        if not force:
            ok, why = self.available()
            if not ok:
                raise LlmUnavailable(why)
        elif not self.api_key:
            raise LlmUnavailable("aucune clé OpenRouter configurée")
        model = model or self.default_model
        payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature,
                   "usage": {"include": True}, "reasoning": {"effort": "low"}}
        t0 = time.monotonic()
        err: str | None = None
        text, usage, cost = "", {}, None
        try:
            async with self._client(timeout or float(settings_store.get("llm_timeout_s"))) as c:
                for attempt in (1, 2):
                    r = await c.post("/chat/completions", content=json.dumps(payload))
                    if r.status_code >= 400:
                        raise LlmError(self._clean(self._http_error(r)))
                    data = r.json()
                    if data.get("error"):
                        e = data["error"] or {}
                        raise LlmError(self._clean(f"OpenRouter : {e.get('message', 'erreur')} {self._raw(e)}".strip()))
                    choice = (data.get("choices") or [{}])[0]
                    msg = choice.get("message") or {}
                    text = (msg.get("content") or "").strip()
                    usage = data.get("usage") or {}
                    cost = usage.get("cost")
                    reasoning = (msg.get("reasoning") or "").strip() if isinstance(msg.get("reasoning"), str) else ""
                    if text:
                        break
                    if attempt == 1 and choice.get("finish_reason") == "length":
                        payload["max_tokens"] = max(payload["max_tokens"] * 4, 1200)  # réponse tronquée par le raisonnement : une relance
                        continue
                    if allow_reasoning_text and reasoning:
                        text = f"(raisonnement) {reasoning[:200]}"
                    break
            if not text:
                raise LlmError("réponse vide du modèle (il a épuisé ses jetons en raisonnement) : choisissez un autre modèle ou augmentez la limite")
        except httpx.TimeoutException:
            err = "délai dépassé"
        except httpx.HTTPError as e:
            err = self._clean(f"réseau: {e}")
        except ValueError:
            err = "réponse non JSON"
        except LlmError as e:
            err = str(e)
        latency = int((time.monotonic() - t0) * 1000)
        with session_scope() as s:
            s.add(LlmUsage(agent_id=agent_id, model=model, prompt_tokens=int(usage.get("prompt_tokens") or 0),
                           completion_tokens=int(usage.get("completion_tokens") or 0), cost_usd=cost, latency_ms=latency,
                           ok=err is None, error=err))
        if err:
            self.last_error = err
            raise LlmError(err)
        self.last_ok_at = utcnow()
        self.last_error = None
        return {"text": text, "model": model, "latency_ms": latency, "cost_usd": cost,
                "prompt_tokens": int(usage.get("prompt_tokens") or 0), "completion_tokens": int(usage.get("completion_tokens") or 0)}

    @staticmethod
    def _raw(e: dict) -> str:
        raw = (e.get("metadata") or {}).get("raw")
        return f"[{str(raw)[:160]}]" if raw else ""

    def _http_error(self, r: httpx.Response) -> str:
        try:
            e = r.json().get("error") or {}
            detail = f"{e.get('message', '')} {self._raw(e)}".strip()
        except ValueError:
            detail = r.text[:160]
        hint = {
            401: "clé invalide ou révoquée",
            402: "crédits insuffisants : ce modèle est payant ou le compte n'a pas de crédit (essayez un modèle « :free »)",
            403: "accès refusé (modération ou région)",
            404: "modèle introuvable ou aucun fournisseur compatible avec vos paramètres de confidentialité OpenRouter (https://openrouter.ai/settings/privacy, requis pour les modèles gratuits)",
            408: "délai dépassé côté fournisseur",
            429: "limite de requêtes atteinte (les modèles gratuits ont des quotas journaliers et par minute)",
            502: "le fournisseur du modèle est indisponible",
            503: "aucun fournisseur disponible pour ce modèle pour l'instant",
        }.get(r.status_code, "")
        return f"OpenRouter {r.status_code} : {detail}" + (f" — {hint}" if hint else "")

    def stats(self) -> dict[str, Any]:
        with session_scope() as s:
            rows = list(s.execute(select(LlmUsage.model, func.count(LlmUsage.id), func.coalesce(func.avg(LlmUsage.latency_ms), 0),
                                         func.coalesce(func.sum(LlmUsage.cost_usd), 0.0), func.coalesce(func.sum(func.iif(LlmUsage.ok, 0, 1)), 0))
                                  .group_by(LlmUsage.model)))
            recent = [{"ts": u.ts.isoformat() + "Z", "agent": u.agent_id, "model": u.model, "ok": u.ok, "latency_ms": u.latency_ms,
                       "tokens": u.prompt_tokens + u.completion_tokens, "cost_usd": u.cost_usd, "error": u.error}
                      for u in s.scalars(select(LlmUsage).order_by(LlmUsage.id.desc()).limit(25))]
        return {"today": self.usage_today(), "by_model": [{"model": r[0], "calls": r[1], "avg_latency_ms": int(r[2]), "cost_usd": float(r[3]),
                                                           "errors": int(r[4])} for r in rows],
                "recent": recent, "last_error": self.last_error, "last_ok_at": self.last_ok_at.isoformat() + "Z" if self.last_ok_at else None}
