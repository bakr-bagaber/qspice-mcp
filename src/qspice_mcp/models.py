"""Model / subcircuit discovery across QSPICE's bundled libraries."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .config import cache_dir, extra_symbol_dirs, qspice_dir

_IDX = None
_PAT = re.compile(r"^\s*\.(model|subckt)\s+(\S+)\s*([^\s(]*)", re.I | re.M)


def _index():
    global _IDX
    if _IDX is not None:
        return _IDX
    files = []
    for d in [qspice_dir()] + extra_symbol_dirs():
        if d.exists():
            for ext in ("*.txt", "*.lib", "*.mod", "*.sub", "*.cir", "*.inc"):
                files += [p for p in d.rglob(ext) if p.stat().st_size < 40_000_000]
    sig = f"{len(files)}:{sum(int(p.stat().st_mtime) for p in files)}"
    cf = cache_dir() / "models.json"
    if cf.exists():
        try:
            d = json.loads(cf.read_text(encoding="utf-8"))
            if d.get("sig") == sig:
                _IDX = d["items"]
                return _IDX
        except Exception:
            pass
    items = []
    for p in files:
        try:
            t = p.read_text(encoding="latin-1", errors="replace")
        except OSError:
            continue
        if p.name in ("RevisionHistory.txt",):
            continue
        for m in _PAT.finditer(t):
            kind, name, typ = m.group(1).lower(), m.group(2), m.group(3)
            if kind == "subckt":
                line = t[m.start(): t.find("\n", m.start())]
                pins = [x for x in line.split()[2:] if "=" not in x and x.lower() != "params:"]
                items.append({"kind": "subckt", "name": name, "pins": pins[:40], "file": str(p)})
            else:
                items.append({"kind": "model", "name": name, "type": typ.upper(), "file": str(p)})
    cf.write_text(json.dumps({"sig": sig, "items": items}), encoding="utf-8")
    _IDX = items
    return items


def search(query: str = "", kind: str | None = None, type_: str | None = None, limit: int = 50) -> list[dict]:
    q = query.lower().split()
    out = []
    for it in _index():
        if kind and it["kind"] != kind:
            continue
        if type_ and it.get("type", "").upper() != type_.upper():
            continue
        hay = (it["name"] + " " + it.get("type", "") + " " + Path(it["file"]).name).lower()
        if all(w in hay for w in q):
            d = dict(it)
            d["library"] = Path(it["file"]).name
            out.append(d)
    out.sort(key=lambda d: (len(d["name"]), d["name"]))
    return out[:limit]


def get_text(name: str, file: str | None = None, max_chars=8000) -> str:
    for it in _index():
        if it["name"].lower() == name.lower() and (file is None or Path(it["file"]).name.lower() == Path(file).name.lower()):
            t = Path(it["file"]).read_text(encoding="latin-1", errors="replace")
            m = re.search(r"^\s*\.(model|subckt)\s+" + re.escape(it["name"]) + r"\b", t, re.I | re.M)
            if not m:
                continue
            start = m.start()
            if it["kind"] == "subckt":
                e = re.search(r"^\s*\.ends\b.*$", t[start:], re.I | re.M)
                end = start + (e.end() if e else max_chars)
            else:
                # model continues on '+' lines
                end = start
                lines = t[start:].split("\n")
                acc = [lines[0]]
                for l in lines[1:]:
                    if l.lstrip().startswith("+"):
                        acc.append(l)
                    else:
                        break
                return "\n".join(acc)[:max_chars]
            return t[start:end][:max_chars]
    raise KeyError(f"model/subckt {name} not found")
