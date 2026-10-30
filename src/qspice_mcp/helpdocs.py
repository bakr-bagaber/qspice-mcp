"""Searchable access to the QSPICE help (QSPICE.chm), decompiled once with hh.exe."""
from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
from pathlib import Path

from .config import cache_dir, qspice_dir


def chm_html_dir() -> Path:
    return cache_dir() / "help" / "chm" / "html"


def ensure_help() -> Path:
    out = cache_dir() / "help"
    idx = out / "index.json"
    chm = qspice_dir() / "QSPICE.chm"
    if idx.exists() and chm.exists() and idx.stat().st_mtime >= chm.stat().st_mtime:
        return out
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / "q.chm"
    if chm.exists():
        shutil.copyfile(chm, tmp)  # hh.exe dislikes spaces in paths
        subprocess.run(["hh.exe", "-decompile", "chm", "q.chm"], cwd=str(out), timeout=120)
    pages = {}
    for f in sorted((out / "chm" / "html").glob("*.htm")):
        pages[f.stem] = _strip(f.read_text(encoding="latin-1", errors="replace"))
    idx.write_text(json.dumps(pages), encoding="utf-8")
    return out


def _strip(t: str) -> str:
    t = re.sub(r"(?is)<(script|style).*?</\1>", "", t)
    t = re.sub(r"(?i)<br\s*/?>", "\n", t)
    t = re.sub(r"(?i)</(p|tr|li|h\d|div|table|pre)>", "\n", t)
    t = re.sub(r"(?i)</t[dh]>", "\t", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    lines = [re.sub(r"[ \t]+", " ", l).strip() for l in t.splitlines()]
    return "\n".join(l for l in lines if l)


_PAGES: dict | None = None


def pages() -> dict:
    global _PAGES
    if _PAGES is None:
        d = ensure_help()
        _PAGES = json.loads((d / "index.json").read_text(encoding="utf-8"))
    return _PAGES


def topics() -> list[str]:
    return sorted(pages())


def get(topic: str) -> str:
    p = pages()
    key = topic.strip()
    cands = [key, key.upper(), key.lstrip("."), key.lstrip(".").upper(), f"{key.upper()}-device",
             f"{key[:1].upper()}-device"]
    for c in cands:
        if c in p:
            return p[c]
    low = {k.lower(): k for k in p}
    for c in cands:
        if c.lower() in low:
            return p[low[c.lower()]]
    hits = search(topic, 1)
    if hits:
        return p[hits[0]["topic"]]
    raise KeyError(f"no help topic '{topic}'. Topics: {', '.join(topics())}")


def search(query: str, limit: int = 8) -> list[dict]:
    words = [w.lower() for w in re.findall(r"[\w.+\-]+", query)]
    res = []
    for k, v in pages().items():
        vl = v.lower()
        score = sum(vl.count(w) for w in words) + 25 * sum(w in k.lower() for w in words)
        if score:
            i = min([vl.find(w) for w in words if w in vl] or [0])
            res.append({"topic": k, "score": score, "snippet": v[max(0, i - 120): i + 280].replace("\n", " ")})
    res.sort(key=lambda d: -d["score"])
    return res[:limit]
