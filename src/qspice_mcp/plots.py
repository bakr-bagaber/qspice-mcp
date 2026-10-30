"""Waveform plotting (matplotlib, headless) -> PNG bytes."""
from __future__ import annotations

import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.ticker import EngFormatter  # noqa: E402

from .qraw import Plot  # noqa: E402


def plot_traces(plot: Plot, exprs: list[str], steps: list[int] | None = None, title: str | None = None,
                xlim=None, separate: bool = False, width=10, height=None, extra_series: dict | None = None) -> bytes:
    steps = steps or list(range(1, plot.n_steps + 1))
    if len(steps) > 12:
        steps = steps[:: max(1, len(steps) // 12)]
    is_ac = plot.complex and "freq" in plot.variables[0][0].lower()
    xname = plot.variables[0][0]
    if is_ac:
        nrow = 2
    else:
        nrow = len(exprs) if separate else 1
    height = height or (3.0 * nrow + 0.8)
    fig, axes = plt.subplots(nrow, 1, figsize=(width, height), sharex=True, squeeze=False)
    axes = axes[:, 0]
    info = plot.step_info()
    for k, e in enumerate(exprs):
        for s in steps:
            x = plot.axis(s)
            y = plot.trace(e, s)
            lab = e
            if plot.n_steps > 1:
                pv = {kk: vv for kk, vv in info[s - 1].items() if kk not in ("step", "points")}
                lab += " [" + (", ".join(f"{kk}={_eng(vv)}" for kk, vv in pv.items()) or f"step {s}") + "]"
            if is_ac:
                axes[0].semilogx(np.real(x), 20 * np.log10(np.abs(y) + 1e-300), label=lab)
                axes[1].semilogx(np.real(x), np.unwrap(np.angle(y)) * 180 / np.pi, label=lab)
            else:
                ax = axes[k] if separate else axes[0]
                ax.plot(np.real(x), np.real(y), label=lab, lw=1.2)
    if extra_series:
        for lab, (x, y) in extra_series.items():
            axes[0].plot(x, y, label=lab, lw=1.2, ls="--")
    if is_ac:
        axes[0].set_ylabel("Magnitude [dB]")
        axes[1].set_ylabel("Phase [deg]")
        axes[1].set_xlabel("Frequency [Hz]")
    else:
        axes[-1].set_xlabel(xname)
        axes[-1].xaxis.set_major_formatter(EngFormatter(unit="s" if xname.lower() == "time" else ""))
        for ax in axes:
            ax.yaxis.set_major_formatter(EngFormatter())
    for ax in axes:
        ax.grid(True, which="both", alpha=.3)
        ax.legend(fontsize=8, loc="best")
        if xlim:
            ax.set_xlim(*xlim)
    fig.suptitle(title or f"{plot.plotname} — {plot.title.lstrip('* ')}", fontsize=11)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return buf.getvalue()


def _eng(v: float) -> str:
    return EngFormatter(places=3)(v).replace(" ", "")
