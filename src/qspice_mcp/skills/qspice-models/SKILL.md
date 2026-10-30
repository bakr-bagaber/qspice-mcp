---
name: qspice-models
description: Device models in QSPICE - which models are bundled, how to find them (search_models), how to write .model statements for diodes/BJTs/MOSFETs/switches, how to use vendor .lib/.subckt files, and common model errors.
---

# Models and libraries

## 1. How a part gets its model

- Primitive parts (D, Q, M, J, ...) use a `.model NAME TYPE params...`. The part's **value** is the model
  NAME (e.g. value `1N4148` on a `D` symbol).
- QSPICE's standard symbols already point to QSPICE's own libraries (Diode.txt, NPN.txt, PNP.txt,
  NMOS.txt, PMOS.txt, NJF.txt, PJF.txt, Zener.txt, Schottky.txt). Any model in those files works
  without extra directives.
- Sub-circuit models (`.subckt`) need an `X` part with a symbol whose pins match the `.subckt` line
  (`qspice_guide('symbols')`) and a `.lib <file>` (added automatically when the symbol knows its file).

## 2. Find a model

```
search_models('1N4148')                 # by name
search_models('', model_type='NMOS')    # all NMOS models
search_models('schottky')               # by library file name
get_model_text('1N4148')                # the .model text
```

Bundled examples: diodes `1N4148`, `1N4007`, `1N5819` (Schottky); MOSFETs `2N7002`, `IRF640`;
JFETs `2N5457`, `J310`; Zetex/Diodes Inc. BJTs `ZTX...`; EPC GaN FETs (`EPC2001` ... as symbols).
Not bundled: `2N3904`, `2N3906`, `2N2222`, `BC547` - add a `.model` line (section 3).

## 3. Writing .model lines (Mike's style: no commas, no parentheses)

```
.model 2N3904 NPN Is=6.734f Bf=416.4 Vaf=74.03 Ikf=66.78m Rb=10 Rc=1 Cjc=3.638p Cje=4.493p Tf=301.2p Tr=239.5n
.model 2N3906 PNP Is=1.41f Bf=180.7 Vaf=18.7 Ikf=80m Rb=10 Rc=2.5 Cjc=9.728p Cje=8.063p Tf=179.3p Tr=33.42n
.model DFAST D Is=2.5n Rs=.5 N=1.75 Cjo=4p Tt=6n Bv=100 Ibv=100u
.model DZ5V1 D Bv=5.1 Ibv=5m Rs=2
.model NFET VDMOS Vto=2.5 Kp=5 Rd=10m Rs=5m Cgdmax=500p Cgdmin=50p Cgs=1n
.model PFET VDMOS pchan Vto=-2.5 Kp=3 Rd=20m Rs=10m Cgdmax=500p Cgdmin=50p Cgs=1n
.model NJ NJF Vto=-2 Beta=1m Lambda=10m
```

- Power MOSFETs: use type `VDMOS` (add `pchan` for P-channel) on the `NMOS`/`PMOS` symbols.
- Simple ideal switch (no model): the `SW` symbol with params `Ron`, `Roff`, `Vt`, `Vh`.
- Long `.model` lines are fine; the server wraps them into `+` continuation lines on the sheet.

## 4. Vendor model files

1. Save the vendor file somewhere permanent (e.g. `C:/models/vendor.lib`).
2. For `.model` files: add `.lib C:/models/vendor.lib` to the directives; set the part value to the model name.
3. For `.subckt` files: `symbol_from_subckt(source='C:/models/vendor.lib', subckt_name='PARTNAME')`, then
   use the new symbol. Its `.lib` is added automatically.
4. Encrypted models work the same way (QSPICE decrypts internally).
5. `add_symbol_folder('C:/models')` makes a folder's models searchable with `search_models`.

## 5. Parameters and corners

- `.param` + braces: `.param Rload=10` and value `{Rload}`; step it with `.step param Rload list 1 10 100`.
- Temperature: `.temp 25 85 125` or `.dc temp -40 125 5`.
- Tolerances / Monte Carlo: use expressions with `gauss()`/`flat()` in values and `.step param run 1 20 1`.

## 6. Model errors

| Message mentions | Fix |
|---|---|
| unknown/undefined model `X` | add `.model X ...` or `.lib file`; check spelling (case-insensitive) |
| unknown subcircuit | the `.subckt` file is not included: `.lib path` or recreate the symbol with `library_file` |
| wrong number of nodes | symbol pins do not match the `.subckt` line: recreate with `symbol_from_subckt` |
| model type mismatch (NPN vs PNP) | use the matching symbol (`NPN`/`PNP`, `NMOS`/`PMOS`) |
