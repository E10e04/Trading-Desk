# PyInstaller — backend embarqué. Les skills vendorisés sont chargés par importlib depuis des fichiers : ils doivent être
# copiés tels quels (datas) en plus d'être détectés. À lancer sur la plateforme cible (PyInstaller ne compile pas en croisé).
from PyInstaller.utils.hooks import collect_submodules

# modules de la bibliothèque standard importés dynamiquement par les skills vendorisés (non détectés par l'analyse statique)
STDLIB_SKILLS = ["argparse", "csv", "decimal", "email", "email.utils", "gzip", "hashlib", "shutil", "statistics", "subprocess", "xml", "xml.etree", "xml.etree.ElementTree", "xml.dom.minidom", "zipfile", "zoneinfo", "urllib.request", "urllib.parse", "urllib.error"]
hidden = collect_submodules("app") + collect_submodules("uvicorn") + ["sqlalchemy.dialects.sqlite"] + STDLIB_SKILLS
a = Analysis(["trading_backend.py"], pathex=["."], datas=[("app/vendor", "app/vendor")], hiddenimports=hidden)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="trading-backend", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="trading-backend")
