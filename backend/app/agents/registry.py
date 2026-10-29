"""Registre des 11 agents (un agent par skill du dépôt source)."""
from __future__ import annotations

from .alerts import AlertsAgent
from .analyst import AnalystAgent
from .base import Agent
from .broker import BrokerAgent
from .budget import BudgetAgent
from .entry import EntryAgent
from .exit import ExitAgent
from .news import NewsAgent
from .pelosi import PelosiAgent
from .risk import RiskAgent
from .scanner import ScannerAgent
from .timemachine import TimeMachineAgent

ORDER = ["scanner", "news", "entry", "exit", "timemachine", "budget", "risk", "broker", "alerts", "analyst", "pelosi"]
_CLASSES = [ScannerAgent, NewsAgent, EntryAgent, ExitAgent, TimeMachineAgent, BudgetAgent, RiskAgent, BrokerAgent, AlertsAgent, AnalystAgent, PelosiAgent]
AGENTS: dict[str, Agent] = {c.id: c() for c in _CLASSES}
assert list(AGENTS) == ORDER


def get_agent(agent_id: str) -> Agent:
    try:
        return AGENTS[agent_id]
    except KeyError:
        raise KeyError(f"agent inconnu : {agent_id}") from None
