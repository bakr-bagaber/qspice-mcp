"""Driving the QSPICE executables: QUX (netlister/exporter/GUI), QSPICE64/80
(simulator) and QPOST (.meas / .four post-processor)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from .config import exe, qspice_dir

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _run(args, cwd=None, timeout=600, stdin=None):
    t0 = time.time()
    p = subprocess.run([str(a) for a in args], cwd=cwd, capture_output=True, timeout=timeout,
                       input=stdin, creationflags=CREATE_NO_WINDOW)
    return p.returncode, p.stdout.decode("latin-1", "replace"), p.stderr.decode("latin-1", "replace"), time.time() - t0


def netlist(schematic: str | Path, timeout=120) -> Path:
    """QUX.exe -Netlist  -> <name>.cir next to the schematic."""
    sch = Path(schematic).resolve()
    out = sch.with_suffix(".cir")
    before = out.stat().st_mtime if out.exists() else 0
    rc, so, se, _ = _run([exe("QUX.exe"), "-Netlist", sch], cwd=sch.parent, timeout=timeout)
    if not out.exists() or out.stat().st_mtime < before:
        raise RuntimeError(f"QUX failed to netlist {sch}: rc={rc} {so} {se}")
    return out


def read_text(p: Path) -> str:
    return Path(p).read_text(encoding="latin-1", errors="replace")


def parse_log(text: str) -> dict:
    lines = [l for l in text.splitlines() if l.strip()]
    errors = [l for l in lines if re.search(r"\b(error|fatal|cannot|can't|failed|unknown|singular|not found|undefined|timestep too small|no such)\b", l, re.I)]
    warnings = [l for l in lines if re.search(r"\bwarning\b", l, re.I)]
    m = re.search(r"Total elapsed time:\s*([\d.eE+-]+)", text)
    steps = re.findall(r"(\d+) of (\d+) steps?:\s*(.*)", text)
    return {"errors": errors, "warnings": warnings, "elapsed_s": float(m.group(1)) if m else None,
            "steps": [s[2].strip() for s in steps]}


def has_post_commands(netlist_text: str) -> bool:
    return bool(re.search(r"^\s*\.(meas|measure|four)\b", netlist_text, re.I | re.M))


def simulate(path: str | Path, engine: str = "QSPICE64", ascii: bool = False, floaters: bool = False,
             extra_args: list | None = None, timeout: float = 900) -> dict:
    """Run a .qsch (netlisted first) or a netlist file.  Returns paths, log, meas."""
    p = Path(path).resolve()
    if p.suffix.lower() == ".qsch":
        cir = netlist(p)
    else:
        cir = p
    eng = engine if engine.lower().endswith(".exe") else engine + ".exe"
    log = cir.with_suffix(".log")
    raw = cir.with_suffix(".qraw")
    args = [exe(eng)]
    if ascii:
        args.append("-ASCII")
    if floaters:
        args.append("-floaters")
    args += list(extra_args or [])
    args += ["-o", log, "-r", raw, cir]
    if raw.exists():
        try:
            raw.unlink()
        except OSError:
            pass
    rc, so, se, dt = _run(args, cwd=cir.parent, timeout=timeout)
    logtxt = read_text(log) if log.exists() else so
    info = parse_log(logtxt + "\n" + se)
    res = {"netlist": str(cir), "log": str(log), "qraw": str(raw) if raw.exists() else None,
           "returncode": rc, "wall_time_s": round(dt, 3), "log_text": logtxt.strip()[-6000:], **info}
    if se.strip():
        res["stderr"] = se.strip()[-3000:]
    if not raw.exists():
        res.setdefault("errors", []).append("no waveform file produced")
    ntxt = read_text(cir)
    res["netlist_text"] = ntxt if len(ntxt) < 12000 else ntxt[:12000] + "\n...(truncated)"
    if raw.exists() and has_post_commands(ntxt):
        res["measurements"] = qpost(cir, raw)
    return res


def qpost(netlist_path: str | Path, raw: str | Path | None = None, timeout=300) -> dict:
    cir = Path(netlist_path).resolve()
    out = cir.with_suffix(".meas.txt")
    args = [exe("QPOST.exe"), cir, "-o", out]
    if raw:
        args += ["-r", Path(raw).resolve()]
    rc, so, se, _ = _run(args, cwd=cir.parent, timeout=timeout)
    text = read_text(out) if out.exists() else so
    return {"text": text.strip(), "results": parse_meas(text)}


_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|nan|inf|-inf"


def _meas_value(s: str):
    """'(  0.846733,  0)  1000' -> {'value': .846733, 'imag': 0, 'at': 1000};
    '0.538753' -> 0.538753"""
    s = s.strip()
    m = re.match(r"\(\s*(" + _NUM + r")\s*,\s*(" + _NUM + r")\s*\)\s*(.*)$", s, re.I)
    if m:
        d = {"value": _f(m.group(1)), "imag": _f(m.group(2))}
        rest = m.group(3).split()
        if rest:
            d["at"] = _f(rest[0])
        if d["imag"] == 0 and "at" not in d:
            return d["value"]
        return d
    toks = s.split()
    if not toks:
        return None
    if len(toks) == 1:
        return _f(toks[0])
    return {"value": _f(toks[0]), "at": _f(toks[1])} if re.fullmatch(_NUM, toks[1], re.I) else _f(toks[0])


def parse_meas(text: str) -> dict:
    """Parse QPOST output into {name: value | {value, imag, at} | [per-step values]}."""
    res: dict = {}
    raw: dict = {}
    cur = None
    for line in text.splitlines():
        st = line.strip()
        if not st:
            continue
        if st.lower().startswith(".meas") or st.lower().startswith(".four"):
            toks = st.rstrip(":").split()
            name = toks[2] if len(toks) > 2 and toks[1].lower() in ("tran", "ac", "dc", "op", "noise", "tf") else (toks[1] if len(toks) > 1 else st)
            cur = name
            raw[cur] = []
            continue
        if cur is None:
            m2 = re.match(r"\s*(\w+)\s*[=:]\s*(.+)$", line)
            if m2:
                res[m2.group(1)] = _meas_value(m2.group(2))
            continue
        raw[cur].append(st)
    for k, lines in raw.items():
        if len(lines) == 1:
            # one run: "value", "value  at" (FIND..AT) or "value (at Time=..)" - no step index
            res[k] = _meas_value(lines[0])
        elif lines:
            # .step runs: "<step#>  value [at]" per line
            vals = []
            for i, st in enumerate(lines):
                m = re.match(r"(\d+)\s+(.*)$", st)
                vals.append(_meas_value(m.group(2) if m and int(m.group(1)) == i + 1 else st))
            res[k] = vals
        else:
            res[k] = None
    return res


def _f(s: str):
    try:
        return float(s)
    except ValueError:
        return s


def export(qraw: str | Path, exprs: list[str], npoints: int | None = None, fmt: str = "CSV",
           out: str | Path | None = None, timeout=300) -> str:
    """QUX.exe -Export: evaluate arbitrary waveform expressions. Returns text."""
    raw = Path(qraw).resolve()
    args = [exe("QUX.exe"), "-Export", raw, ",".join(exprs)]
    if npoints:
        args.append(str(int(npoints)))
    args.append(fmt.upper())
    args.append("-stdout")
    rc, so, se, _ = _run(args, cwd=raw.parent, timeout=timeout)
    if out:
        Path(out).write_text(so, encoding="utf-8")
    if not so.strip():
        raise RuntimeError(f"QUX export produced no output (rc={rc}) {se}")
    return so


def dll_variables(schematic: str | Path, timeout=120) -> str:
    sch = Path(schematic).resolve()
    rc, so, se, _ = _run([exe("QUX.exe"), "-DLLvariables", sch, "-stdout"], cwd=sch.parent, timeout=timeout)
    return so or se


def protect(netlist_path: str | Path, mode: str = "subcircuits", backup: bool = True) -> dict:
    """Encrypt .prot sections or subcircuit bodies (QSPICE overwrites the file!)."""
    p = Path(netlist_path).resolve()
    bak = None
    if backup:
        bak = p.with_name(p.stem + ".unprotected" + p.suffix)
        shutil.copyfile(p, bak)
    sw = "-ProtectSubcircuits" if mode.startswith("sub") else "-ProtectSelections"
    rc, so, se, _ = _run([exe("QSPICE64.exe"), sw, p], cwd=p.parent)
    return {"file": str(p), "backup": str(bak) if bak else None, "returncode": rc, "output": (so + se).strip()}


def open_gui(path: str | Path | None = None, run: bool = False, tool: str = "QUX.exe") -> dict:
    args = [str(exe(tool))]
    if path:
        p = str(Path(path).resolve())
        args += (["-Run", p] if run else [p])
    subprocess.Popen(args, cwd=str(qspice_dir()), close_fds=True,
                     creationflags=0x00000008 | 0x00000200 if os.name == "nt" else 0)
    return {"launched": args}


def version() -> dict:
    d = {"install_dir": str(qspice_dir())}
    try:
        from .config import cache_dir
        sim = exe("QSPICE64.exe")
        vf = cache_dir() / "version.txt"
        key = f"{int(sim.stat().st_mtime)}"
        if vf.exists() and vf.read_text().startswith(key + "|"):
            d["simulator_build"] = vf.read_text().split("|", 1)[1]
        else:
            tmp = cache_dir() / "ver.cir"
            tmp.write_text("* version probe\nR1 a 0 1\nV1 a 0 1\n.op\n.end\n")
            _run([sim, "-ASCII", "-o", tmp.with_suffix(".log"), "-r", tmp.with_suffix(".qraw"), tmp], cwd=tmp.parent, timeout=60)
            txt = read_text(tmp.with_suffix(".qraw"))
            m = re.search(r"Command:\s*(.*)", txt)
            build = m.group(1).strip() if m else "unknown"
            vf.write_text(key + "|" + build)
            d["simulator_build"] = build
    except Exception as e:  # pragma: no cover
        d["simulator_build"] = f"unknown ({e})"
    rh = qspice_dir() / "RevisionHistory.txt"
    if rh.exists():
        d["revision_history_head"] = read_text(rh).strip().splitlines()[:6]
    d["executables"] = sorted(p.name for p in qspice_dir().glob("*.exe"))
    return d
