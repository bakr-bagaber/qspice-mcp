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


def index_text() -> str:
    lines = ["QSPICE MCP guides (call qspice_guide with a name):", ""]
    for d in info():
        lines.append(f"- {d['name']}: {d['description']}")
    return "\n".join(lines)
