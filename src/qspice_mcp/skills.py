"""Access to the bundled skills / guides (Markdown files with YAML front matter)."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parent / "skills"


def _front(text: str) -> dict:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    out = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                out[k.strip()] = v.strip().strip('"')
    return out


def names() -> list[str]:
    if not ROOT.exists():
        return []
    return sorted(p.name for p in ROOT.iterdir() if (p / "SKILL.md").exists())


def info() -> list[dict]:
    out = []
    for n in names():
        t = (ROOT / n / "SKILL.md").read_text(encoding="utf-8")
        f = _front(t)
        out.append({"name": n, "description": f.get("description", ""), "chars": len(t)})
    return out


def resolve(name: str) -> str:
    ns = names()
    key = name.strip().lower().replace(" ", "-").replace("_", "-")
    for n in ns:
        if n == key:
            return n
    for n in ns:
        if n.endswith(key) or n == "qspice-" + key:
            return n
    for n in ns:
        if key in n:
            return n
    raise KeyError(f"no guide '{name}'. Available: {', '.join(ns)}")


def get(name: str) -> str:
    n = resolve(name)
    p = ROOT / n / "SKILL.md"
    text = p.read_text(encoding="utf-8")
    extra = sorted(x for x in (ROOT / n).glob("*.md") if x.name != "SKILL.md")
    for x in extra:
        text += f"\n\n<!-- {x.name} -->\n" + x.read_text(encoding="utf-8")
    return text


SHORT = {"qspice-overview": "overview", "qspice-schematic-drafting": "drafting", "qspice-auto-draw": "auto-draw",
         "qspice-canvas-editing": "canvas", "qspice-symbols": "symbols", "qspice-simulation": "simulation",
         "qspice-waveforms": "waveforms", "qspice-models": "models", "qspice-netlist-import": "netlist-import",
         "qspice-troubleshooting": "troubleshooting", "qspice-recipes": "recipes"}
ORDER = list(SHORT)


def index_text() -> str:
    lines = ["QSPICE MCP guides. Call qspice_guide('<short name>'), e.g. qspice_guide('drafting').",
             "Read 'overview' first, 'drafting' before drawing, 'simulation' before simulating.", ""]
    items = sorted(info(), key=lambda d: ORDER.index(d["name"]) if d["name"] in ORDER else 99)
    for d in items:
        short = SHORT.get(d["name"], d["name"])
        lines.append(f"- {short}  ({d['name']}): {d['description']}")
    return "\n".join(lines)
