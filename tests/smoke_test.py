"""End-to-end smoke test through the real MCP stdio protocol.

Run:  .venv\\Scripts\\python.exe tests\\smoke_test.py
Writes results to tests\\out\\ (images + log) and exits non-zero on failure.
"""
import asyncio
import base64
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)
FAIL = []


def grab(name, res):
    texts = []
    n = 0
    for c in res.content:
        if c.type == "text":
            texts.append(c.text)
        elif c.type == "image":
            n += 1
            (OUT / f"{name}_{n}.png").write_bytes(base64.b64decode(c.data))
    t = "\n".join(texts)
    if res.is_error:
        FAIL.append(f"{name}: {t[:300]}")
    print(f"[{'FAIL' if res.is_error else ' ok '}] {name} ({n} image(s))", flush=True)
    return t


async def main():
    params = StdioServerParameters(command=sys.executable, args=["-m", "qspice_mcp.server"],
                                   env={**os.environ, "QSPICE_MCP_WORKDIR": str(OUT)})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            print(f"{len(tools)} tools", flush=True)
            grab("qspice_info", await s.call_tool("qspice_info", {}))
            spec = {"title": "RC low-pass", "components": [
                {"ref": "V1", "symbol": "V", "value": "AC 1", "nodes": ["in", "0"]},
                {"ref": "R1", "symbol": "R", "value": "1K", "nodes": ["in", "out"]},
                {"ref": "C1", "symbol": "C", "value": "100n", "nodes": ["out", "0"]}],
                "directives": [".ac dec 50 10 1Meg", ".meas ac f3db WHEN mag(V(out))=1/sqrt(2)"]}
            t = grab("auto_draw", await s.call_tool("auto_draw_schematic",
                                                    {"spec": spec, "path": "rc.qsch", "simulate_after": True}))
            d = json.loads(t)
            if not d["validation"]["ok"]:
                FAIL.append("rc validation")
            f3db = d["simulation"]["measurements"]["f3db"]
            if abs(f3db - 1591.5) > 5:
                FAIL.append(f"f3db={f3db}")
            grab("plot", await s.call_tool("plot_waveforms", {"path": "rc.qsch", "traces": ["V(out)"]}))
            grab("render", await s.call_tool("render_schematic", {"path": "rc.qsch", "grid": True}))
            net = ("* diff pair\nV1 vcc 0 5\nV2 vee 0 -5\nV3 inp 0 SINE 0 10m 1K\nQ1 c1 inp e NPN1\n"
                   "Q2 c2 0 e NPN1\nR1 vcc c1 2K\nR2 vcc c2 2K\nI1 e vee 1m\n.model NPN1 NPN Bf=200\n.tran 3m\n")
            t = grab("netlist_to_schematic", await s.call_tool("netlist_to_schematic",
                                                               {"netlist_text": net, "path": "diffpair.qsch"}))
            if not json.loads(t)["validation"]["ok"]:
                FAIL.append("diffpair validation")
            grab("simulate", await s.call_tool("simulate", {"path": "diffpair.qsch"}))
            grab("new", await s.call_tool("new_schematic", {"path": "canvas.qsch", "overwrite": True}))
            grab("place", await s.call_tool("place_component", {"path": "canvas.qsch", "symbol": "R", "x": 0, "y": 0, "value": "1K"}))
            grab("place2", await s.call_tool("place_component", {"path": "canvas.qsch", "symbol": "C", "x": 600, "y": 0, "value": "1n"}))
            grab("connect", await s.call_tool("connect_pins", {"path": "canvas.qsch", "from_": "R1.1", "to": "C1.+"}))
            grab("ground", await s.call_tool("add_ground", {"path": "canvas.qsch", "at": "C1.-"}))
            t = grab("validate", await s.call_tool("validate_schematic", {"path": "canvas.qsch"}))
    print("\nFAILURES:" if FAIL else "\nALL PASSED", *FAIL, sep="\n  ")
    sys.exit(1 if FAIL else 0)


asyncio.run(main())
