"""Génère docs/AGENTS.md depuis le code des agents (source de vérité : classes d'agents). Usage : backend/.venv/bin/python scripts/gen_agents_doc.py"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.agents.registry import AGENTS, ORDER  # noqa: E402
from app.agents.loader import SKILL_FILES, provenance  # noqa: E402

p = provenance()
out = [
    "# Les 11 agents",
    "",
    f"Généré depuis le code (`scripts/gen_agents_doc.py`). Source des skills : {p['repo']} @ `{p['commit']}` ({p['license']}).",
    "Les skills sont copiés **sans modification** dans `backend/app/vendor/day_trader_skill/` ; chaque agent les appelle et ajoute uniquement l'adaptation (voir « Écarts »).",
    "",
    "| Agent | Skill | Implémentation | IA |",
    "|---|---|---|---|",
]
for a in ORDER:
    g = AGENTS[a]
    out.append(f"| {g.name} | `{SKILL_FILES[a][0]}` | {'complète' if g.implementation == 'full' else '**partielle**'} | {'optionnelle (résumé)' if g.uses_llm else 'non'} |")
out += ["", "**Pas d'IA dans les calculs** : prix, quantités, risque, stops et cibles sont déterministes. L'IA ne rédige que des résumés de faits déjà calculés.", ""]
for a in ORDER:
    g = AGENTS[a]
    out += [f"## {g.name} (`{a}`)", "", f"**Mission.** {g.mission}", "", f"**Skill source.** `{SKILL_FILES[a][0]}` — implémentation **{'complète' if g.implementation == 'full' else 'partielle'}**.", ""]
    for title, items in (("Entrées", g.inputs), ("Sorties", g.outputs), ("Dépendances", g.dependencies)):
        out += [f"**{title}.**", ""] + [f"- {x}" for x in items] + [""]
    if g.default_params:
        out += ["**Paramètres par défaut.**", ""] + [f"- `{k}` = `{v}`" + (f" — {g.param_help[k]}" if k in g.param_help else "") for k, v in g.default_params.items()] + [""]
    out += ["**Écarts avec le skill d'origine et limites.**", ""] + [f"- {x}" for x in g.limitations] + [""]
(ROOT / "docs").mkdir(exist_ok=True)
(ROOT / "docs" / "AGENTS.md").write_text("\n".join(out), encoding="utf-8")
print("docs/AGENTS.md écrit")
