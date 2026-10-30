"""Locating QSPICE and per-user working folders."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

_CANDIDATES = [
    r"C:\Program Files\QSPICE",
    r"C:\Program Files (x86)\QSPICE",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\QSPICE"),
    r"C:\QSPICE",
]


@lru_cache(maxsize=1)
def qspice_dir() -> Path:
    env = os.environ.get("QSPICE_DIR")
    if env and Path(env, "QUX.exe").exists():
        return Path(env)
    for c in _CANDIDATES:
        if Path(c, "QUX.exe").exists():
            return Path(c)
    # registry lookup (uninstall key)
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for sub in (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"):
                try:
                    k = winreg.OpenKey(hive, sub)
                except OSError:
                    continue
                for i in range(winreg.QueryInfoKey(k)[0]):
                    try:
                        sk = winreg.OpenKey(k, winreg.EnumKey(k, i))
                        name = winreg.QueryValueEx(sk, "DisplayName")[0]
                        if "QSPICE" in name:
                            loc = winreg.QueryValueEx(sk, "InstallLocation")[0]
                            if Path(loc, "QUX.exe").exists():
                                return Path(loc)
                    except OSError:
                        continue
    except ImportError:
        pass
    return Path(_CANDIDATES[0])


def exe(name: str) -> Path:
    return qspice_dir() / name


def cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    p = Path(base) / "qspice-mcp"
    p.mkdir(parents=True, exist_ok=True)
    return p


def work_dir() -> Path:
    """Default folder for files the server creates when no path is given."""
    env = os.environ.get("QSPICE_MCP_WORKDIR")
    p = Path(env) if env else Path.home() / "Documents" / "QSPICE-MCP"
    p.mkdir(parents=True, exist_ok=True)
    return p


def extra_symbol_dirs() -> list[Path]:
    env = os.environ.get("QSPICE_SYMBOL_PATH", "")
    out = [Path(x) for x in env.split(os.pathsep) if x.strip()]
    docs = Path.home() / "Documents" / "QSPICE"
    if docs.exists():
        out.append(docs)
    return out


def help_dir() -> Path:
    return cache_dir() / "help"
