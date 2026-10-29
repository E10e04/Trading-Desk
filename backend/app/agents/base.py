"""Socle commun des 11 agents : identité, paramètres, résultat structuré, contexte d'exécution."""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from ..bus import EventBus
from ..llm.openrouter import OpenRouterClient
from ..market.feed import MarketFeed
from ..paper.engine import PaperEngine
from .adapt import SessionMapper, Virtual, make_virtual, mapper_for
from .loader import skill_version

log = logging.getLogger(__name__)
Clock = Callable[[], dt.datetime]


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


@dataclass
class AgentResult:
    status: str = "ok"  # ok | partial | skipped | error
    summary: str = ""
    output: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    data_quality: str = "none"  # live | stale | historical | simulated | none
    instrument: str | None = None
    model: str | None = None  # modèle IA réellement utilisé, le cas échéant


class AgentError(Exception):
    pass


@dataclass
class AgentContext:
    feed: MarketFeed
    engine: PaperEngine
    bus: EventBus
    llm: OpenRouterClient
    http: httpx.AsyncClient
    clock: Clock = utc_now
    correlation_id: str | None = None
    agent_params: dict[str, Any] = field(default_factory=dict)  # paramètres de CET agent (défauts + surcharges)
    agent_model: str | None = None  # modèle IA configuré pour cet agent
    results: dict[str, AgentResult] = field(default_factory=dict)  # résultats des agents amont (même exécution)

    def virtual(self, symbol: str) -> tuple[Any, Virtual, SessionMapper]:
        inst = self.feed.info(symbol)
        q = self.feed.quotes.get(symbol)
        v = make_virtual(inst, self.feed.fx_to_usd(inst.quote_ccy), q.mid if q else None)
        return inst, v, mapper_for(inst, self.agent_params.get("session_anchor_utc") or self.global_param("session_anchor_utc"))

    def global_param(self, name: str) -> Any:
        from .. import settings_store
        return settings_store.get(name) if name in settings_store.DEFAULTS else self._pipeline_defaults().get(name)

    @staticmethod
    def _pipeline_defaults() -> dict[str, Any]:
        return {"session_anchor_utc": "07:00"}

    def quality(self, symbol: str) -> str:
        f = self.feed.freshness(symbol)["status"]
        return {"live": "live", "stale": "stale", "closed": "historical", "unknown": "none"}.get(f, "none")


class Agent:
    """Contrat commun. Chaque agent a sa propre logique, construite sur les fonctions de son skill source."""

    id: str = ""
    name: str = ""
    mission: str = ""
    skill: str = ""  # dossier du skill source
    implementation: str = "full"  # full | partial
    limitations: list[str] = []  # écarts connus entre le skill original et l'agent intégré
    dependencies: list[str] = []  # services externes ou agents amont
    uses_llm: bool = False
    default_params: dict[str, Any] = {}
    param_help: dict[str, str] = {}
    default_interval_s: int | None = None  # planification automatique par défaut
    inputs: list[str] = []
    outputs: list[str] = []

    async def run(self, ctx: AgentContext, **inputs: Any) -> AgentResult:  # pragma: no cover - interface
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "mission": self.mission, "skill": self.skill, "skill_version": skill_version(),
                "implementation": self.implementation, "limitations": self.limitations, "dependencies": self.dependencies,
                "uses_llm": self.uses_llm, "default_params": self.default_params, "param_help": self.param_help,
                "default_interval_s": self.default_interval_s, "inputs": self.inputs, "outputs": self.outputs}


def params_of(agent: Agent, ctx: AgentContext) -> dict[str, Any]:
    p = dict(agent.default_params)
    p.update(ctx.agent_params or {})
    return p


async def llm_summary(ctx: AgentContext, agent_id: str, system: str, facts: dict[str, Any], warnings: list[str],
                      max_tokens: int = 350) -> tuple[str | None, str | None]:
    """Résumé IA OPTIONNEL à partir de faits déjà calculés. Retourne (texte, modèle) ou (None, None) si l'IA est indisponible.
    L'IA ne reçoit que des faits vérifiés et ne produit jamais de chiffres utilisés ailleurs."""
    import json
    from ..llm.openrouter import LlmError, LlmUnavailable
    ok, why = ctx.llm.available()
    if not ok:
        warnings.append(f"résumé IA non généré : {why}")
        return None, None
    try:
        out = await ctx.llm.chat([{"role": "system", "content": system},
                                  {"role": "user", "content": json.dumps(facts, ensure_ascii=False, default=str)[:12000]}],
                                 model=ctx.agent_model, agent_id=agent_id, max_tokens=max_tokens)
        return out["text"], out["model"]
    except (LlmError, LlmUnavailable) as e:
        warnings.append(f"résumé IA non généré : {e}")
        return None, None
