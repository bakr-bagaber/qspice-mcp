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


# What the special QSPICE device pages are about (their titles are just a symbol)
_TITLE_EXTRA = {
    "Atilde-device": "op-amp opamp operational amplifier comparator",
    "Oslash-device": "dll c++ verilog digital module code block",
    "Cross-device": "transformer",
    "Euro-device": "dac digital to analog converter",
    "Sterling-device": "digital comparator encoder gate driver multiplexer",
    "Yen-device": "logic gate flip flop digital and or xor inverter",
    "B-device": "behavioral behavioural arbitrary expression b-source bv bi",
    "S-device": "switch voltage controlled",
    "W-device": "switch current controlled",
    "K-device": "coupling transformer mutual inductance",
    "X-device": "subcircuit instance",
    "MEAS": "meas measure measurement find when avg rms max min",
    "STEP": "step sweep parameter sweep",
    "PARAM": "param parameter variable",
    "TRAN": "tran transient time domain",
    "AC": "ac small signal frequency response bode",
    "OP": "op operating point bias dc",
    "WaveformExpressions": "expression functions waveform math",
    "FOUR": "fourier fft harmonics thd",
    "NOISE": "noise",
    "LIB": "lib library include model file",
    "INC": "include file",
    "MODEL": "model statement",
    "OPTION": "options option tolerance reltol abstol gmin",
    "SUBCKT": "subcircuit subckt ends",
    "Laplace": "laplace transfer function s-domain",
}
# plain words -> pages that are certainly relevant
_SYN = {
    "behavioral": ["B-device"], "behavioural": ["B-device"], "b-source": ["B-device"],
    "mosfet": ["M-device"], "nmos": ["M-device"], "pmos": ["M-device"], "fet": ["M-device", "J-device"],
    "bjt": ["Q-device"], "npn": ["Q-device"], "pnp": ["Q-device"], "transistor": ["Q-device", "M-device"],
    "diode": ["D-device"], "zener": ["D-device"], "schottky": ["D-device"], "led": ["D-device"],
    "resistor": ["R-device"], "capacitor": ["C-device"], "inductor": ["L-device"],
    "jfet": ["J-device"], "transformer": ["Cross-device", "K-device"], "coupling": ["K-device"],
    "opamp": ["Atilde-device"], "op-amp": ["Atilde-device"], "comparator": ["Atilde-device"],
    "switch": ["S-device", "W-device"], "dll": ["Oslash-device"], "verilog": ["Oslash-device"],
    "c++": ["Oslash-device"], "logic": ["Yen-device"], "gate": ["Yen-device"], "dac": ["Euro-device"],
    "measure": ["MEAS"], "meas": ["MEAS"], "measurement": ["MEAS"], "sweep": ["STEP", "DC"],
    "step": ["STEP"], "param": ["PARAM"], "parameter": ["PARAM"], "subcircuit": ["SUBCKT", "X-device"],
    "subckt": ["SUBCKT"], "transient": ["TRAN"], "tran": ["TRAN"], "fourier": ["FOUR"], "fft": ["FOUR"],
    "thd": ["FOUR"], "noise": ["NOISE"], "option": ["OPTION"], "options": ["OPTION"],
    "include": ["INC", "LIB"], "library": ["LIB"], "operating": ["OP"], "bias": ["OP"],
    "laplace": ["Laplace"], "expression": ["WaveformExpressions", "B-device"],
    "vcvs": ["E-device"], "cccs": ["F-device"], "vccs": ["G-device"], "ccvs": ["H-device"],
    "hierarchy": ["Hierarchy"], "hierarchical": ["Hierarchy"], "symbol": ["SymbolEditor"],
    "python": ["Python_API"], "qpost": ["QPOST"], "qux": ["QUX"], "bode": ["AC", "BODE"],
    "sine": ["V-device"], "sin": ["V-device"], "pulse": ["V-device"], "pwl": ["V-device"],
}
_PHRASES = {"voltage source": ["V-device"], "current source": ["I-device"],
            "operating point": ["OP"], "transmission line": ["T-device", "O-device"],
            "initial condition": ["IC"], "frequency response": ["AC", "BODE"]}
_STOP = set("how to a an the in of what is are for and or do does i with on use using can my it be by "
             "from at as which that this set make write get show me please qspice spice".split())
_IDX: dict | None = None


def _index() -> dict:
    global _IDX
    if _IDX is None:
        from collections import Counter
        docs = {}
        df = Counter()
        for k, v in pages().items():
            toks = re.findall(r"[\w+\-]+", v.lower())
            tf = Counter(toks)
            title = (v.split("\n", 1)[0] + " " + k + " " + _TITLE_EXTRA.get(k, "")).lower()
            docs[k] = (tf, len(toks), title)
            df.update(set(tf))
        n = len(docs)
        avg = sum(d[1] for d in docs.values()) / max(1, n)
        _IDX = {"docs": docs, "df": df, "n": n, "avg": avg}
    return _IDX


def search(query: str, limit: int = 8) -> list[dict]:
    """BM25 over the help pages + title/keyword boosts + a small synonym map, so plain
    questions ('behavioral voltage source', 'how to sweep a parameter') find the right page."""
    import math
    ix = _index()
    q = query.lower()
    words = [w for w in re.findall(r"[\w+\-]+", q) if (len(w) > 1 or w.isdigit()) and w not in _STOP]
    words = [w[:-1] if len(w) > 4 and w.endswith("s") and w[:-1] in ix["df"] else w for w in words]
    boost: dict = {}
    for w in words:
        for t in _SYN.get(w, []):
            boost[t] = boost.get(t, 0) + 8.0
    for ph, ts in _PHRASES.items():
        if ph in q:
            for t in ts:
                boost[t] = boost.get(t, 0) + 12.0
    res = []
    k1, b = 1.4, 0.6
    for k, (tf, ln, title) in ix["docs"].items():
        score = 0.0
        for w in words:
            df = ix["df"].get(w, 0)
            idf = math.log(1 + (ix["n"] - df + .5) / (df + .5))
            f = tf.get(w, 0)
            if f:
                score += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * ln / ix["avg"]))
            if re.search(r"(?<![\w])" + re.escape(w) + r"(?![\w])", title):
                score += 2.5 * idf + 2
        score += boost.get(k, 0)
        if score > 0.5:
            v = pages()[k]
            vl = v.lower()
            pos = [vl.find(w) for w in words if w in vl]
            i = min(pos) if pos else 0
            res.append({"topic": k, "title": v.split("\n", 1)[0][:80], "score": round(score, 1),
                        "snippet": v[max(0, i - 100): i + 260].replace("\n", " ")})
    res.sort(key=lambda d: -d["score"])
    return res[:limit]
