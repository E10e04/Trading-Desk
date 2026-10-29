"""Orchestrateur des agents.

Responsabilités :
  * exécuter un agent avec verrou (pas de concurrence contradictoire), délai maximal, reprises, audit complet (table agent_runs) ;
  * planifier les agents périodiques (Scanner, News, Alerts, Pelosi, surveillance flat-by d'Exit) ;
  * pipeline événementiel : à chaque clôture de bougie 5 min d'un instrument surveillé, Entry évalue le signal ; s'il est déclenché,
    la chaîne Exit → TimeMachine → Budget → Risk → Broker s'exécute pour chaque portefeuille éligible. Un seul déclenchement par
    instrument/setup/séance/portefeuille ;
  * toute proposition passe par le contrôle Risk ; le Broker refuse sans PASS. Aucun agent ne peut contourner ce circuit ;
  * interrupteur d'arrêt (`kill_switch`), `pipeline_enabled`, désactivation par agent, exécution automatique paper (`auto_execute`)
    ou approbation manuelle ordre par ordre.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
import uuid
from typing import Any

import httpx
from sqlalchemy import delete, select

from . import settings_store
from .agents.alerts import AlertsAgent
from .agents.base import Agent, AgentContext, AgentResult, utc_now
from .agents.loader import skill_version
from .agents.registry import AGENTS, ORDER, get_agent
from .bus import EventBus
from .db import session_scope
from .llm.openrouter import OpenRouterClient
from .market.feed import MarketFeed
from .models import AgentConfig, AgentRun, Portfolio, Proposal, Strategy, WatchItem, utcnow
from .notify import notify
from .paper.engine import PaperEngine

log = logging.getLogger(__name__)

TIMEOUTS = {"scanner": 180, "news": 60, "analyst": 60, "pelosi": 120, "timemachine": 90, "alerts": 20}
DEFAULT_TIMEOUT = 30
RETRIES = {"scanner": 1, "news": 1, "analyst": 1, "pelosi": 1}
MAX_OUTPUT_BYTES = 150_000
PIPELINE_AGENTS = ["entry", "exit", "timemachine", "budget", "risk", "broker"]
SCHEDULED = {"scanner": None, "news": 900, "alerts": None, "pelosi": None}  # None -> default_interval_s de l'agent
ALWAYS_ON_JOBS = {"alerts", "exit_monitor"}  # tournent même si le pipeline est coupé (protection / information)
EXIT_MONITOR_S = 30


def jsonable(obj: Any, max_bytes: int = MAX_OUTPUT_BYTES) -> Any:
    try:
        text = json.dumps(obj, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return {"unserializable": True}
    if len(text) <= max_bytes:
        return json.loads(text)
    data = json.loads(text)
    if isinstance(data, dict):
        keep = {k: v for k, v in data.items() if len(json.dumps(v, default=str)) < max_bytes // 8}
        keep["truncated"] = True
        keep["dropped_keys"] = [k for k in data if k not in keep]
        return keep
    return {"truncated": True}


class Orchestrator:
    def __init__(self, feed: MarketFeed, engine: PaperEngine, bus: EventBus, llm: OpenRouterClient, http: httpx.AsyncClient | None = None,
                 clock=utc_now) -> None:
        self.feed, self.engine, self.bus, self.llm = feed, engine, bus, llm
        self.http = http or httpx.AsyncClient(timeout=30)
        self.clock = clock
        self._locks: dict[str, asyncio.Lock] = {}
        self._chain_sem = asyncio.Semaphore(3)
        self._tasks: set[asyncio.Task] = set()
        self._scheduler: asyncio.Task | None = None
        self._last_job: dict[str, float] = {}
        self._watch_cache: tuple[float, list[str]] = (0.0, [])
        self._processed: set[str] = set()
        self.counters = {"bars_seen": 0, "entries_checked": 0, "signals": 0, "proposals": 0, "executed": 0, "blocked": 0, "errors": 0}
        self.started_at: dt.datetime | None = None
        self._running: dict[str, dt.datetime] = {}

    # ------------------------------------------------------------------ cycle de vie
    async def start(self) -> None:
        self.started_at = utcnow()
        self.bus.subscribe("bar_closed", self._on_bar)
        self._scheduler = asyncio.create_task(self._scheduler_loop(), name="orchestrator-scheduler")

    async def stop(self) -> None:
        if self._scheduler:
            self._scheduler.cancel()
            try:
                await self._scheduler
            except (asyncio.CancelledError, Exception):
                pass
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    # ------------------------------------------------------------------ configuration par agent
    def config(self, agent_id: str) -> dict[str, Any]:
        a = get_agent(agent_id)
        with session_scope() as s:
            row = s.get(AgentConfig, agent_id)
            cfg = {"enabled": row.enabled if row else True, "model": row.model if row else None, "params": dict(row.params or {}) if row else {},
                   "interval_s": row.interval_s if row else None}
        cfg["effective_params"] = {**a.default_params, **cfg["params"]}
        cfg["timeout_s"] = float(cfg["effective_params"].get("timeout_s") or TIMEOUTS.get(agent_id, DEFAULT_TIMEOUT))
        cfg["retries"] = int(cfg["effective_params"].get("retries", RETRIES.get(agent_id, 0)))
        return cfg

    def set_config(self, agent_id: str, enabled: bool | None = None, model: str | None = None, params: dict | None = None,
                   interval_s: int | None = None, clear_model: bool = False) -> dict[str, Any]:
        a = get_agent(agent_id)
        if params:
            bad = [k for k in params if k not in a.default_params and k not in ("timeout_s", "retries")]
            if bad:
                raise KeyError(f"paramètre(s) inconnu(s) pour {agent_id} : {', '.join(bad)}")
        with session_scope() as s:
            row = s.get(AgentConfig, agent_id)
            if not row:
                row = AgentConfig(agent_id=agent_id, enabled=True, params={})
                s.add(row)
            if enabled is not None:
                row.enabled = bool(enabled)
            if model is not None:
                row.model = model or None
            if clear_model:
                row.model = None
            if params is not None:
                row.params = {**(row.params or {}), **params}
            if interval_s is not None:
                row.interval_s = interval_s
        return self.config(agent_id)

    def reset_config(self, agent_id: str) -> dict[str, Any]:
        with session_scope() as s:
            row = s.get(AgentConfig, agent_id)
            if row:
                s.delete(row)
        return self.config(agent_id)

    def _context(self, agent_id: str, correlation_id: str | None, results: dict[str, AgentResult] | None, overrides: dict | None = None) -> AgentContext:
        cfg = self.config(agent_id)
        params = dict(cfg["effective_params"])
        params.update(overrides or {})
        if agent_id == "timemachine" and params.get("min_edge") is None:
            me = settings_store.get("timemachine_min_edge")
            params["min_edge"] = me if isinstance(me, (int, float)) and not isinstance(me, bool) else None
        return AgentContext(feed=self.feed, engine=self.engine, bus=self.bus, llm=self.llm, http=self.http, clock=self.clock,
                            correlation_id=correlation_id, agent_params=params, agent_model=cfg["model"] or None, results=results if results is not None else {})

    # ------------------------------------------------------------------ exécution d'un agent (audit complet)
    async def run_agent(self, agent_id: str, trigger: str = "manual", correlation_id: str | None = None, results: dict[str, AgentResult] | None = None,
                        overrides: dict | None = None, **inputs: Any) -> AgentResult:
        agent = get_agent(agent_id)
        cfg = self.config(agent_id)
        instrument = (inputs.get("symbol") or (inputs.get("plan") or {}).get("symbol") or (inputs.get("sizing") or {}).get("symbol") or "") or None
        if instrument:
            instrument = str(instrument).upper()
        lock_key = f"{agent_id}:{instrument or '*'}:{inputs.get('portfolio_id', '')}"
        warnings: list[str] = []
        if not cfg["enabled"]:
            if trigger != "manual":
                return AgentResult("skipped", f"{agent.name} désactivé.", {"disabled": True})
            warnings.append("agent désactivé : exécution manuelle explicite uniquement")
        lock = self._locks.setdefault(lock_key, asyncio.Lock())
        if lock.locked():
            if trigger != "manual":
                return AgentResult("skipped", f"{agent.name} déjà en cours sur {instrument or 'tous les instruments'}.", {"busy": True})
        run_id = self._open_run(agent, instrument, trigger, correlation_id, inputs, cfg)
        res: AgentResult | None = None
        err: str | None = None
        t0 = time.monotonic()
        async with lock:
            self._running[lock_key] = utcnow()
            try:
                for attempt in range(cfg["retries"] + 1):
                    ctx = self._context(agent_id, correlation_id, results, overrides)
                    try:
                        res = await asyncio.wait_for(agent.run(ctx, **inputs), timeout=cfg["timeout_s"])
                        err = None
                        break
                    except asyncio.TimeoutError:
                        err = f"délai dépassé ({cfg['timeout_s']:.0f} s)"
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001
                        err = f"{type(e).__name__}: {str(e)[:300]}"
                        log.exception("agent %s en échec", agent_id)
                    if attempt < cfg["retries"]:
                        await asyncio.sleep(min(1.0 * (attempt + 1), 3.0))
                        warnings.append(f"tentative {attempt + 1} échouée ({err}), nouvelle tentative")
            finally:
                self._running.pop(lock_key, None)
        dur = int((time.monotonic() - t0) * 1000)
        if res is None:
            res = AgentResult("error", f"{agent.name} : {err}", {"error": err}, [], "none", instrument)
            self.counters["errors"] += 1
            notify(self.bus, "error", agent_id, f"{agent.name} en échec", err or "", instrument, dedupe_key=f"agent-error:{agent_id}", throttle_s=300)
        res.warnings = warnings + list(res.warnings or [])
        self._close_run(run_id, res, err, dur)
        return res

    def _open_run(self, agent: Agent, instrument: str | None, trigger: str, cid: str | None, inputs: dict, cfg: dict) -> int:
        safe_in = jsonable({k: v for k, v in inputs.items() if k not in ("proposal",)}, 20_000)
        with session_scope() as s:
            row = AgentRun(agent_id=agent.id, skill_version=skill_version(), model=cfg["model"] if agent.uses_llm else None, instrument=instrument,
                           trigger=trigger, correlation_id=cid, input=safe_in, status="running")
            s.add(row)
            s.flush()
            rid = row.id
        self.bus.publish_nowait("agent_run", {"id": rid, "agent": agent.id, "status": "running", "instrument": instrument, "trigger": trigger})
        return rid

    def _close_run(self, run_id: int, res: AgentResult, err: str | None, dur: int) -> None:
        with session_scope() as s:
            row = s.get(AgentRun, run_id)
            row.finished_at = utcnow()
            row.duration_ms = dur
            row.status = res.status if res.status in ("ok", "partial", "skipped", "error") else "ok"
            row.output = jsonable(res.output)
            row.summary = res.summary
            row.warnings = list(res.warnings or [])[:20]
            row.error = err
            row.data_quality = res.data_quality
            if res.model:
                row.model = res.model
            view = {"id": run_id, "agent": row.agent_id, "status": row.status, "instrument": row.instrument, "summary": row.summary, "duration_ms": dur}
        self.bus.publish_nowait("agent_run", view)

    def purge_old_runs(self, days: int = 30) -> int:
        cut = utcnow() - dt.timedelta(days=days)
        with session_scope() as s:
            n = s.execute(delete(AgentRun).where(AgentRun.started_at < cut)).rowcount
        return int(n or 0)

    # ------------------------------------------------------------------ planification
    async def _scheduler_loop(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("planificateur en échec")
            await asyncio.sleep(1.0)

    def _due(self, job: str, every: float, now: float) -> bool:
        last = self._last_job.get(job)
        if last is None:
            self._last_job[job] = now - every + min(every, 5.0)  # premier passage ~5 s après le démarrage
            return False
        if now - last >= every:
            self._last_job[job] = now
            return True
        return False

    async def _tick(self) -> None:
        now = time.monotonic()
        pipeline = bool(settings_store.get("pipeline_enabled"))
        for aid in ("scanner", "news", "alerts", "pelosi"):
            cfg = self.config(aid)
            every = cfg["interval_s"] if cfg["interval_s"] is not None else (SCHEDULED[aid] or get_agent(aid).default_interval_s)
            if not every or not cfg["enabled"]:
                continue
            if aid not in ALWAYS_ON_JOBS and not pipeline:
                continue
            if self._due(aid, float(every), now):
                self._spawn(self.run_agent(aid, trigger="schedule"))
        if self.config("exit")["enabled"] and self._due("exit_monitor", EXIT_MONITOR_S, now):
            self._spawn(self._exit_monitor())

    async def _exit_monitor(self) -> None:
        res = await self.run_agent("exit", trigger="schedule", mode="monitor")
        for c in res.output.get("closed", []):
            notify(self.bus, "info", "exit", f"Position {c['symbol']} clôturée à l'heure flat-by", "Sortie forcée du plan (paper).", c["symbol"])

    def _spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    # ------------------------------------------------------------------ pipeline événementiel
    def watch_symbols(self) -> list[str]:
        t, cached = self._watch_cache
        if time.monotonic() - t < 20:
            return cached
        now = utcnow()
        with session_scope() as s:
            syms = {w.symbol for w in s.scalars(select(WatchItem)) if not w.expires_at or w.expires_at > now}
        out = sorted(syms)
        self._watch_cache = (time.monotonic(), out)
        return out

    def invalidate_watch(self) -> None:
        self._watch_cache = (0.0, [])

    async def _on_bar(self, topic: str, payload: dict) -> None:
        if payload.get("tf") != "5m":
            return
        self.counters["bars_seen"] += 1
        sym = payload["symbol"]
        if not settings_store.get("pipeline_enabled") or settings_store.get("kill_switch"):
            return
        if sym in self.watch_symbols():
            self._spawn(self.process_symbol(sym, trigger="event"))

    def _targets(self) -> list[tuple[int, dict]]:
        """Portefeuilles éligibles (+ paramètres de stratégie). Si des stratégies sont activées, elles décident ; sinon tous les portefeuilles actifs."""
        with session_scope() as s:
            strategies = list(s.scalars(select(Strategy).where(Strategy.enabled.is_(True))))
            strat = [(st.portfolio_id, dict(st.params or {}) | {"strategy_id": st.id, "strategy": st.name}) for st in strategies]
            if strat:
                return [(pid, p) for pid, p in strat if pid is not None]
            return [(p.id, {}) for p in s.scalars(select(Portfolio).where(Portfolio.active.is_(True))) if (p.risk or {}).get("auto_trade", True)]

    async def process_symbol(self, symbol: str, trigger: str = "event", setup: str | None = None, level: float | None = None) -> list[dict]:
        """Évalue le signal d'entrée d'un instrument et, s'il est déclenché, lance la chaîne pour chaque portefeuille éligible."""
        symbol = symbol.upper()
        cid = uuid.uuid4().hex[:16]
        results: dict[str, AgentResult] = {}
        self.counters["entries_checked"] += 1
        targets = self._targets()
        sp = targets[0][1] if targets else {}
        entry = await self.run_agent("entry", trigger, cid, results, overrides={k: sp[k] for k in ("or_minutes", "no_entries_before", "no_entries_after") if k in sp},
                                     symbol=symbol, setup=setup or sp.get("setup"), level=level)
        results["entry"] = entry
        if entry.output.get("status") != "triggered":
            return []
        self.counters["signals"] += 1
        key = entry.output["signal_key"]
        out = []
        news_res = await self._news_for(symbol, cid, results)
        for pid, params in targets:
            dedupe = f"{key}|{pid}"
            if dedupe in self._processed or self._proposal_exists(dedupe):
                continue
            self._processed.add(dedupe)
            async with self._chain_sem:
                out.append(await self.run_chain(symbol, pid, dict(results), cid, params, dedupe, news_res))
        return out

    async def _news_for(self, symbol: str, cid: str, results: dict[str, AgentResult]) -> AgentResult | None:
        cfg = self.config("news")
        if not cfg["enabled"]:
            return None
        res = await self.run_agent("news", "pipeline", cid, results, symbols=[symbol])
        if res.status == "error":
            res.warnings.append("actualités indisponibles : la proposition continue sans elles (information seulement)")
        return res

    @staticmethod
    def _proposal_exists(dedupe: str) -> bool:
        with session_scope() as s:
            for p in s.scalars(select(Proposal).where(Proposal.status.in_(("pending", "blocked", "approved", "executed", "rejected")))
                               .order_by(Proposal.id.desc()).limit(500)):
                if (p.context or {}).get("signal_key") == dedupe:
                    return True
        return False

    async def run_chain(self, symbol: str, pid: int, results: dict[str, AgentResult], cid: str, params: dict, dedupe: str,
                        news: AgentResult | None = None) -> dict:
        """Exit → TimeMachine → Budget → Risk → (Broker). Une proposition est TOUJOURS enregistrée (exécutée, bloquée, refusée ou en attente)."""
        entry = results["entry"]
        trig = entry.output["trigger"]
        ov = {k: params[k] for k in ("stop", "target", "flat_by") if k in params}
        prop_id = self._new_proposal(symbol, pid, entry, params, cid, dedupe)
        try:
            xr = await self.run_agent("exit", "pipeline", cid, results, overrides=ov, symbol=symbol, entry=trig["price"], opening_range=entry.output.get("opening_range"),
                                      setup=entry.output["setup"], entry_time=trig["closes_at"])
            results["exit"] = xr
            if xr.status != "ok":
                return self._finish_proposal(prop_id, "rejected", f"plan impossible : {xr.summary}", plan=xr.output)
            plan = xr.output["plan"]
            self._update_proposal(prop_id, plan=plan)
            tm = await self.run_agent("timemachine", "pipeline", cid, results, plan=plan)
            results["timemachine"] = tm
            bd = await self.run_agent("budget", "pipeline", cid, results, plan=plan, portfolio_id=pid)
            results["budget"] = bd
            self._update_proposal(prop_id, sizing=bd.output, context_extra={"timemachine": self._brief(tm), "news": self._brief(news)})
            if bd.status == "error" or bd.output.get("refusal") or float(bd.output.get("qty") or 0) <= 0:
                return self._finish_proposal(prop_id, "rejected", bd.output.get("refusal") or bd.summary)
            rk = await self.run_agent("risk", "pipeline", cid, results, overrides=self._risk_overrides(), sizing=bd.output, signal=entry.output,
                                      timemachine=tm.output, news=news.output if news else None, portfolio_id=pid)
            results["risk"] = rk
            self._update_proposal(prop_id, risk=rk.output)
            if rk.output.get("verdict") != "PASS":
                self.counters["blocked"] += 1
                notify(self.bus, "warning", "risk", f"{symbol} bloqué par Risk", "; ".join(rk.output.get("reasons", [])[:3]), symbol)
                return self._finish_proposal(prop_id, "blocked", "; ".join(rk.output.get("reasons", [])[:3]))
            self.counters["proposals"] += 1
            prop = {"portfolio_id": pid, "symbol": symbol, "sizing": bd.output, "risk": rk.output, "plan": plan, "proposal_id": prop_id}
            br = await self.run_agent("broker", "pipeline", cid, results, proposal=prop)
            return self._after_broker(prop_id, symbol, pid, plan, br)
        except Exception as e:  # noqa: BLE001
            log.exception("chaîne en échec pour %s", symbol)
            self.counters["errors"] += 1
            return self._finish_proposal(prop_id, "rejected", f"erreur interne : {type(e).__name__}: {str(e)[:200]}")

    def _risk_overrides(self) -> dict:
        e = self.config("entry")["effective_params"]
        return {"no_entries_before": e.get("no_entries_before"), "no_entries_after": e.get("no_entries_after")}

    @staticmethod
    def _brief(r: AgentResult | None) -> dict | None:
        if not r:
            return None
        o = r.output
        keep = {k: o[k] for k in ("verdict", "edge", "p_target", "p_stop", "gate", "insufficient_data", "risk_flags", "note") if k in o}
        return {"status": r.status, "summary": r.summary, **jsonable(keep, 8000)}

    def _after_broker(self, prop_id: int, symbol: str, pid: int, plan: dict, br: AgentResult) -> dict:
        st = br.output.get("status")
        if st == "executed":
            self.counters["executed"] += 1
            o = br.output["order"]
            AlertsAgent.add_from_plan(plan, pid)
            notify(self.bus, "success", "broker", f"{symbol} : position ouverte (papier)", br.summary, symbol)
            return self._finish_proposal(prop_id, "executed", br.summary, order_ids=[o["id"]], decision=br.output.get("decision", "auto"))
        if st == "pending_approval":
            notify(self.bus, "info", "broker", f"{symbol} : en attente de votre accord", " / ".join(br.output.get("ticket", [])), symbol)
            return self._finish_proposal(prop_id, "pending", br.summary, decision="user")
        if st == "working":
            o = br.output["order"]
            return self._finish_proposal(prop_id, "approved", br.summary, order_ids=[o["id"]], decision=br.output.get("decision", "auto"))
        return self._finish_proposal(prop_id, "rejected", br.summary)

    async def simulate(self, symbol: str, portfolio_id: int, entry: float | None = None, stop: float | None = None, target: float | None = None,
                       stop_rule: str | None = None, target_rule: str | None = None, setup: str = "manual") -> dict:
        """Simulation à blanc (aucun ordre, aucune proposition) : plan -> TimeMachine -> taille -> contrôle Risk. Sert la page Simulateur."""
        symbol = symbol.upper()
        cid = uuid.uuid4().hex[:16]
        results: dict[str, AgentResult] = {}
        ov = {"stop": stop_rule or (f"price:{stop}" if stop is not None else "pct:0.3"), "target": target_rule or (f"price:{target}" if target is not None else "2R")}
        xr = await self.run_agent("exit", "manual", cid, results, overrides=ov, symbol=symbol, entry=entry, setup=setup, opening_range=None)
        results["exit"] = xr
        out: dict[str, Any] = {"correlation_id": cid, "symbol": symbol, "exit": {"status": xr.status, "summary": xr.summary, **xr.output}}
        if xr.status != "ok":
            return out
        plan = xr.output["plan"]
        tm = await self.run_agent("timemachine", "manual", cid, results, plan=plan)
        bd = await self.run_agent("budget", "manual", cid, results, plan=plan, portfolio_id=portfolio_id)
        results["budget"] = bd
        out["timemachine"] = {"status": tm.status, "summary": tm.summary, **self._strip(tm.output)}
        out["budget"] = {"status": bd.status, "summary": bd.summary, **self._strip(bd.output)}
        if bd.output.get("virtual_order"):
            rk = await self.run_agent("risk", "manual", cid, results, overrides=self._risk_overrides(), sizing=bd.output, signal=None, timemachine=tm.output,
                                      portfolio_id=portfolio_id)
            out["risk"] = {"status": rk.status, "summary": rk.summary, **{k: v for k, v in rk.output.items() if k not in ("skill_result", "checked_order")}}
        out["executed"] = False
        return out

    @staticmethod
    def _strip(o: dict) -> dict:
        return {k: v for k, v in o.items() if k not in ("virtual_order",)}

    # ------------------------------------------------------------------ propositions
    def _new_proposal(self, symbol: str, pid: int, entry: AgentResult, params: dict, cid: str, dedupe: str) -> int:
        with session_scope() as s:
            p = Proposal(portfolio_id=pid, symbol=symbol, side="buy", setup=entry.output.get("setup", "orb"), kind="entry", status="pending",
                         source_agent="entry", strategy_id=params.get("strategy_id"), correlation_id=cid,
                         plan={}, sizing={}, risk={}, context={"signal_key": dedupe, "signal": jsonable(entry.output, 20_000)}, decision="auto")
            s.add(p)
            s.flush()
            return p.id

    def _update_proposal(self, pid: int, plan: dict | None = None, sizing: dict | None = None, risk: dict | None = None, context_extra: dict | None = None) -> None:
        with session_scope() as s:
            p = s.get(Proposal, pid)
            if plan is not None:
                p.plan = jsonable(plan, 20_000)
            if sizing is not None:
                p.sizing = jsonable(sizing, 40_000)
            if risk is not None:
                p.risk = jsonable(risk, 80_000)
            if context_extra:
                p.context = {**(p.context or {}), **jsonable(context_extra, 20_000)}

    def _finish_proposal(self, prop_id: int, status: str, note: str, plan: dict | None = None, order_ids: list | None = None, decision: str | None = None) -> dict:
        with session_scope() as s:
            p = s.get(Proposal, prop_id)
            p.status = status
            p.note = (note or "")[:1000]
            if plan:
                p.plan = jsonable(plan, 20_000)
            if order_ids:
                p.order_ids = order_ids
            if decision:
                p.decision = decision
            view = self.proposal_view(p)
        self.bus.publish_nowait("proposal", view)
        return view

    @staticmethod
    def proposal_view(p: Proposal) -> dict:
        return {"id": p.id, "ts": p.ts.isoformat() + "Z", "portfolio_id": p.portfolio_id, "symbol": p.symbol, "side": p.side, "setup": p.setup, "kind": p.kind,
                "status": p.status, "source_agent": p.source_agent, "correlation_id": p.correlation_id, "plan": p.plan, "sizing": p.sizing,
                "risk": {k: v for k, v in (p.risk or {}).items() if k not in ("skill_result", "checked_order")}, "context": p.context,
                "order_ids": p.order_ids, "decision": p.decision, "note": p.note}

    def list_proposals(self, status: str | None = None, limit: int = 100) -> list[dict]:
        with session_scope() as s:
            q = select(Proposal).order_by(Proposal.id.desc()).limit(limit)
            if status:
                q = q.where(Proposal.status == status)
            return [self.proposal_view(p) for p in s.scalars(q)]

    async def decide_proposal(self, proposal_id: int, reply: str | None = None, reject: bool = False) -> dict:
        """Approbation manuelle (quand `auto_execute` est désactivé) ou refus. Le contrôle Risk est rejoué avant l'exécution."""
        with session_scope() as s:
            p = s.get(Proposal, proposal_id)
            if not p:
                raise KeyError("proposition introuvable")
            if p.status != "pending":
                raise ValueError(f"proposition déjà {p.status}")
            data = {"symbol": p.symbol, "pid": p.portfolio_id, "plan": dict(p.plan or {}), "sizing": dict(p.sizing or {}), "risk": dict(p.risk or {}),
                    "age_min": (utcnow() - p.ts).total_seconds() / 60.0, "cid": p.correlation_id, "sig": dict((p.context or {}).get("signal") or {})}
        if reject:
            return self._finish_proposal(proposal_id, "rejected", "refusée par l'utilisateur", decision="user")
        if data["age_min"] > float(settings_store.get("proposal_ttl_min") or 15):
            return self._finish_proposal(proposal_id, "expired", f"proposition trop ancienne ({data['age_min']:.0f} min) : le signal doit être réévalué")
        cid = data["cid"]
        rk = await self.run_agent("risk", "manual", cid, {}, overrides=self._risk_overrides(), sizing=data["sizing"], signal=data["sig"], portfolio_id=data["pid"])
        self._update_proposal(proposal_id, risk=rk.output)
        if rk.output.get("verdict") != "PASS":
            return self._finish_proposal(proposal_id, "blocked", "Risk rejoué : " + "; ".join(rk.output.get("reasons", [])[:3]), decision="user")
        prop = {"portfolio_id": data["pid"], "symbol": data["symbol"], "sizing": data["sizing"], "risk": rk.output, "plan": data["plan"], "proposal_id": proposal_id}
        br = await self.run_agent("broker", "manual", cid, {}, proposal=prop, reply=reply or "")
        if br.output.get("status") == "pending_approval":
            return {**self.proposal_view_by_id(proposal_id), "reply_accepted": False, "reason": br.output.get("reason"), "ticket": br.output.get("ticket")}
        return {**self._after_broker(proposal_id, data["symbol"], data["pid"], data["plan"], br), "reply_accepted": True}

    def proposal_view_by_id(self, pid: int) -> dict:
        with session_scope() as s:
            return self.proposal_view(s.get(Proposal, pid))

    def expire_pending(self) -> int:
        ttl = float(settings_store.get("proposal_ttl_min") or 15)
        cut = utcnow() - dt.timedelta(minutes=ttl)
        with session_scope() as s:
            rows = list(s.scalars(select(Proposal).where(Proposal.status == "pending", Proposal.ts < cut)))
            for p in rows:
                p.status, p.note = "expired", "expirée sans décision"
            return len(rows)

    # ------------------------------------------------------------------ diagnostic
    def status(self) -> dict[str, Any]:
        with session_scope() as s:
            last: dict[str, dict] = {}
            for aid in ORDER:
                r = s.scalar(select(AgentRun).where(AgentRun.agent_id == aid).order_by(AgentRun.id.desc()).limit(1))
                if r:
                    last[aid] = {"status": r.status, "at": r.started_at.isoformat() + "Z", "summary": r.summary, "duration_ms": r.duration_ms}
        return {"pipeline_enabled": settings_store.get("pipeline_enabled"), "auto_execute": settings_store.get("auto_execute"),
                "kill_switch": settings_store.get("kill_switch"), "running": {k: v.isoformat() + "Z" for k, v in self._running.items()},
                "counters": dict(self.counters), "watchlist": self.watch_symbols(), "last_runs": last, "tasks": len(self._tasks),
                "started_at": self.started_at.isoformat() + "Z" if self.started_at else None}
