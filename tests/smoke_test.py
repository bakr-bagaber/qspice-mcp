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


def check(cond, msg):
    if not cond:
        FAIL.append(msg)


async def main():
    params = StdioServerParameters(command=sys.executable, args=["-m", "qspice_mcp.server"],
                                   env={**os.environ, "QSPICE_MCP_WORKDIR": str(OUT)})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            print(f"{len(tools)} tools", flush=True)
            prompts = [p.name for p in (await s.list_prompts()).prompts]
            check("draw_circuit" in prompts, "prompts missing")
            grab("qspice_info", await s.call_tool("qspice_info", {}))
            t = grab("guide", await s.call_tool("qspice_guide", {"topic": "drafting"}))
            check("FEEDBACK LOOP" in t, "drafting guide content")
            spec = {"title": "RC low-pass", "components": [
                {"ref": "V1", "symbol": "V", "value": "AC 1", "nodes": ["in", "0"]},
                {"ref": "R1", "symbol": "R", "value": "1kΩ", "nodes": ["in", "out"]},
                {"ref": "C1", "symbol": "C", "value": "100nF", "nodes": ["out", "0"]}],
                "directives": [".ac dec 50 10 1Meg", ".meas f3db WHEN mag(V(out))=1/sqrt(2)"]}
            t = grab("auto_draw", await s.call_tool("auto_draw_schematic",
                                                    {"spec": spec, "path": "rc.qsch", "simulate_after": True}))
            d = json.loads(t)
            check(d["validation"]["ok"], "rc validation")
            check(d["review"]["score"] >= 90, f"rc review {d['review']['score']}")
            f3db = d["simulation"]["measurements"]["f3db"]
            check(abs(f3db - 1591.5) < 5, f"f3db={f3db}")
            t = grab("review", await s.call_tool("review_schematic", {"path": "rc.qsch"}))
            check(json.loads(t)["counts"]["error"] == 0, "review errors")
            t = grab("canvas_map", await s.call_tool("canvas_map", {"path": "rc.qsch"}))
            check("free_space" in t, "canvas_map parts")
            t = grab("add_component", await s.call_tool("add_component", {
                "path": "rc.qsch", "symbol": "R", "value": "10K", "connections": {"1": "C1.1", "2": "GND"}}))
            check(json.loads(t)["review"]["errors"] == 0, "add_component errors")
            t = grab("move", await s.call_tool("move_component", {"path": "rc.qsch", "ref": "C1", "dx": 300}))
            check(json.loads(t)["review"]["errors"] == 0, "move errors")
            grab("reroute", await s.call_tool("reroute_schematic", {"path": "rc.qsch"}))
            grab("texts", await s.call_tool("fix_text_placement", {"path": "rc.qsch"}))
            grab("undo", await s.call_tool("undo_last_edit", {"path": "rc.qsch"}))
            grab("render", await s.call_tool("render_schematic", {"path": "rc.qsch", "annotate": "pins"}))
            grab("plot", await s.call_tool("plot_waveforms", {"path": "rc.qsch", "traces": ["V(out)"]}))
            t = grab("measure", await s.call_tool("measure", {"path": "rc.qsch",
                                                              "statements": [".meas g FIND mag(V(out)) AT 1K"]}))
            check(abs(json.loads(t)["results"]["g"]["value"] - .847) < .01, "measure g")
            net = ("* diff pair\nV1 vcc 0 5\nV2 vee 0 -5\nV3 inp 0 SINE 0 10m 1K\nQ1 c1 inp e NPN1\n"
                   "Q2 c2 0 e NPN1\nR1 vcc c1 2K\nR2 vcc c2 2K\nI1 e vee 1m\n.model NPN1 NPN Bf=200\n.tran 3m\n")
            t = grab("netlist_to_schematic", await s.call_tool("netlist_to_schematic",
                                                               {"netlist_text": net, "path": "diffpair.qsch"}))
            check(json.loads(t)["validation"]["ok"], "diffpair validation")
            t = grab("simulate", await s.call_tool("simulate", {"path": "diffpair.qsch"}))
            check(json.loads(t)["ok"], "diffpair sim")
            # symbols + hierarchy
            t = grab("create_symbol", await s.call_tool("create_symbol", {
                "name": "SMOKEBLK", "pins": ["IN", "EN", "GND", "OUT"], "folder": str(OUT / "symbols")}))
            check(len(json.loads(t)["pins_in_spice_order"]) == 4, "create_symbol pins")
            sub = ".subckt SMOKEAMP inp inn out vcc vee\nE1 out 0 inp inn 100\n.ends SMOKEAMP"
            t = grab("symbol_from_subckt", await s.call_tool("symbol_from_subckt", {"source": sub}))
            check([p["name"] for p in json.loads(t)["pins_in_spice_order"]] == ["inp", "inn", "out", "vcc", "vee"],
                  "subckt pin order")
            grab("create_subsheet", await s.call_tool("create_subsheet", {
                "name": "SMOKERC", "ports": ["IN", "OUT"], "folder": str(OUT),
                "spec": {"components": [{"ref": "R1", "symbol": "R", "value": "1K", "nodes": ["IN", "OUT"]},
                                        {"ref": "C1", "symbol": "C", "value": "1u", "nodes": ["OUT", "0"]}]}}))
            spec2 = {"components": [
                {"ref": "V1", "symbol": "V", "value": "AC 1", "nodes": ["in", "0"]},
                {"ref": "X1", "symbol": str(OUT / "SMOKERC.qsch"), "pins": {"IN": "in", "OUT": "out"}}],
                "directives": [".ac dec 20 10 100K"]}
            t = grab("hier_parent", await s.call_tool("auto_draw_schematic",
                                                      {"spec": spec2, "path": "parent.qsch", "simulate_after": True}))
            d = json.loads(t)
            check(d["validation"]["ok"] and d["simulation"]["ok"], "hierarchy sim")
            # canvas basics
            grab("new", await s.call_tool("new_schematic", {"path": "canvas.qsch", "overwrite": True}))
            grab("place", await s.call_tool("place_component", {"path": "canvas.qsch", "symbol": "R", "x": 0, "y": 0, "value": "1K"}))
            grab("place2", await s.call_tool("place_component", {"path": "canvas.qsch", "symbol": "C", "x": 600, "y": 0, "value": "1n"}))
            grab("connect", await s.call_tool("connect_pins", {"path": "canvas.qsch", "from_pin": "R1.1", "to": "C1.+"}))
            grab("ground", await s.call_tool("add_ground", {"path": "canvas.qsch", "at": "C1.-"}))
            grab("validate", await s.call_tool("validate_schematic", {"path": "canvas.qsch"}))
    print("\nFAILURES:" if FAIL else "\nALL PASSED", *FAIL, sep="\n  ")
    sys.exit(1 if FAIL else 0)


asyncio.run(main())
