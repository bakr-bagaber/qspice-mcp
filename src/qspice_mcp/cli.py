"""Command-line bridge to the QSPICE MCP tools.

Lets any shell-capable agent (or a human) use exactly the same tools an MCP
client gets, without an MCP connection:

    qmcp tools                       list tools (name + one-line summary)
    qmcp help <tool>                 full description + parameters of one tool
    qmcp call <tool> <args>          run a tool; <args> is one of
                                       @C:\\path\\args.json      (JSON file)
                                       "{'path':'rc.qsch'}"      (JSON / Python-style dict)
                                       key=value key2=value2     (simple values)
    qmcp guide [topic]               print a skill / guide (no topic: list them)
    qmcp export-skills [folder]      copy the skills to a folder (default ~/.claude/skills)

Images returned by a tool are saved as PNG files; their paths are printed.
"""
from __future__ import annotations

import ast
import asyncio
import base64
import json
import os
import re
import sys
import time
from pathlib import Path


def _types(prop: dict) -> set:
    t = set()
    if "type" in prop:
        t.add(prop["type"])
    for x in prop.get("anyOf", []) or []:
        if isinstance(x, dict) and "type" in x:
            t.add(x["type"])
    return t


def parse_args(argv: list[str], schema: dict | None = None) -> dict:
    """key=value pairs (typed by the tool's schema: value=10 stays the string '10' for a
    string parameter), a JSON/Python dict, @file.json or file.json, or one bare path."""
    if not argv:
        return {}
    props = (schema or {}).get("properties", {})
    if len(argv) == 1:
        s = argv[0].strip()
        if s.startswith("@"):
            return _loads(Path(s[1:]).read_text(encoding="utf-8-sig"))
        if s.lower().endswith(".json") and Path(s).exists():
            return _loads(Path(s).read_text(encoding="utf-8-sig"))
        if s.startswith("{"):
            return _loads(s)
    out = {}
    for a in argv:
        if "=" not in a:
            # a bare path for a tool whose first parameter is `path`
            if "path" in props and "path" not in out and (re.search(r"\.(qsch|qraw|cir|net|qsym|lib)$", a, re.I)
                                                       or Path(a).exists()):
                out["path"] = a
                continue
            raise SystemExit(f"cannot parse argument '{a}': use key=value, a JSON dict, or a .json file")
        k, v = a.split("=", 1)
        ty = _types(props.get(k, {}))
        if ty == {"string"} or (ty and ty <= {"string", "null"}):
            out[k] = v                      # keep '10', '4.7K', 'True' as text
            continue
        try:
            val = json.loads(v)
            if isinstance(val, (int, float)) and "string" in ty and not ({"integer", "number"} & ty):
                val = v
            out[k] = val
        except json.JSONDecodeError:
            out[k] = _loose_list(v) if v.strip().startswith("[") and v.strip().endswith("]") else v
    return out


def _loose_list(s: str) -> list:
    """'[V(out),V(in)]' or '[a, b]' (quotes eaten by the shell) -> ['V(out)', 'V(in)']."""
    inner = s.strip()[1:-1]
    items, depth, cur = [], 0, ""
    for ch in inner:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            items.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        items.append(cur)
    out = []
    for it in items:
        t = it.strip().strip("'\"")
        try:
            out.append(json.loads(t) if re.fullmatch(r"-?\d+(\.\d+)?", t) else t)
        except json.JSONDecodeError:
            out.append(t)
    return out


def _loads(s: str):
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    t = re.sub(r"\btrue\b", "True", s)
    t = re.sub(r"\bfalse\b", "False", t)
    t = re.sub(r"\bnull\b", "None", t)
    try:
        return ast.literal_eval(t)
    except Exception as e:  # pragma: no cover
        raise SystemExit(f"arguments are not valid JSON: {e}")


def _img_dir() -> Path:
    from .config import work_dir
    d = work_dir() / "_images"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _schema(name: str) -> dict | None:
    from .server import mcp
    for t in await mcp.list_tools():
        if t.name == name:
            return getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {}
    return None


async def _call(name: str, argv: list[str]) -> int:
    from .server import mcp
    schema = await _schema(name)
    if schema is None:
        print(f"no tool named '{name}'. 'qmcp tools' lists them.")
        return 2
    args = parse_args(argv, schema)
    t0 = time.time()
    try:
        res = await mcp.call_tool(name, args)
    except Exception as e:
        print(f"ERROR calling {name}: {type(e).__name__}: {e}")
        return 1
    content = getattr(res, "content", None) or []
    n = 0
    stamp = time.strftime("%H%M%S")
    # keep pictures next to the file the tool worked on (…/images/), else in the work folder
    img_dir = _img_dir()
    stem = ""
    target = args.get("path") or args.get("out_path") or args.get("folder")
    if isinstance(target, str) and target.strip():
        try:
            from .server import _p
            tp = _p(target)
            base = tp if tp.is_dir() else tp.parent
            if base.exists():
                img_dir = base / "images"
                img_dir.mkdir(exist_ok=True)
                stem = (tp.stem + "_") if not tp.is_dir() else ""
        except Exception:
            pass
    for c in content:
        typ = getattr(c, "type", "")
        if typ == "text":
            print(c.text)
        elif typ == "image":
            n += 1
            p = img_dir / f"{stem}{name}_{stamp}_{n}.png"
            p.write_bytes(base64.b64decode(c.data))
            print(f"[image saved: {p}]")
    if getattr(res, "is_error", False):
        print(f"[tool reported an error after {time.time() - t0:.1f}s]")
        return 1
    print(f"[done in {time.time() - t0:.1f}s]")
    return 0


async def _tools(verbose: bool = False):
    from .server import mcp
    tools = await mcp.list_tools()
    for t in tools:
        text = " ".join((t.description or "").split())
        m = re.match(r"(.+?(?<!e\.g)(?<!i\.e)(?<!etc)[.!?])(\s|$)", text)
        desc = m.group(1) if m else text
        if len(desc) > 180:
            desc = desc[:177].rsplit(" ", 1)[0] + " ..."
        print(f"{t.name}: {desc}")
    print(f"\n{len(tools)} tools. 'qmcp help <tool>' shows parameters.")


async def _help(name: str):
    from .server import mcp
    for t in await mcp.list_tools():
        if t.name == name:
            print(f"# {t.name}\n\n{(t.description or '').strip()}\n\nParameters:")
            schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {}
            props = schema.get("properties", {})
            req = set(schema.get("required", []))
            for k, v in props.items():
                typ = v.get("type") or "/".join(x.get("type", "?") for x in v.get("anyOf", []) if isinstance(x, dict))
                dflt = f" (default {v['default']!r})" if "default" in v else ""
                star = " REQUIRED" if k in req else ""
                print(f"  - {k}: {typ}{star}{dflt}")
            return
    print(f"no tool named {name}")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "tools":
        asyncio.run(_tools())
        return 0
    if cmd == "help":
        asyncio.run(_help(rest[0] if rest else ""))
        return 0
    if cmd == "guide":
        from . import skills
        print(skills.get(rest[0]) if rest else skills.index_text())
        return 0
    if cmd == "call":
        if not rest:
            print("usage: qmcp call <tool> <args>")
            return 2
        return asyncio.run(_call(rest[0], rest[1:]))
    if cmd == "export-skills":
        import shutil
        from . import skills
        dest = Path(rest[0]).expanduser() if rest else Path.home() / ".claude" / "skills"
        dest.mkdir(parents=True, exist_ok=True)
        for n in skills.names():
            shutil.copytree(skills.ROOT / n, dest / n, dirs_exist_ok=True)
            print(f"copied {n} -> {dest / n}")
        return 0
    # 'qmcp get_symbol name=V' -> same as 'qmcp call get_symbol name=V'
    if asyncio.run(_schema(cmd)) is not None:
        return asyncio.run(_call(cmd, rest))
    print(f"unknown command {cmd}\n{__doc__}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
