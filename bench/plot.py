"""Turn run summaries into the report's latency figures and table.

Phase 2 needs two figures: mean latency vs. request rate, and p99 (tail) latency
vs. request rate. Phase 3 compares several configurations on the same axes
(1 server vs. 3 servers under each balancing algorithm), so this script already
takes any number of series:  --series "LABEL=DIRECTORY" [--series ...]

It only reads the .summary.json files written by bench.loadgen; the statistics
themselves are computed in one place, bench.stats. With several repetitions per
rate, a point is the mean over repetitions and the whiskers span min..max.

Outputs in --out:  latency_mean.pdf/.png, latency_p99.pdf/.png, latency_table.tex
(PDF for the LaTeX report, PNG for a quick look).

Run (client container):
    python -m bench.plot --series "1 server=results/phase2" --out results/figures
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # containers have no display
import matplotlib.pyplot as plt  # noqa: E402 - must follow matplotlib.use()

# Sized for one IEEE column (3.5 in) with 8 pt text, so figures need no zooming.
# STIX ships with matplotlib and matches the Times font of the IEEE template.
plt.rcParams.update({"font.size": 8, "font.family": "STIXGeneral", "mathtext.fontset": "stix"})
FIGSIZE = (3.5, 2.3)

Series = dict[float, list[dict]]  # rate -> summaries of the repetitions at that rate


def load_series(directory: Path) -> Series:
    """Read every run summary in `directory`, grouped by request rate."""
    by_rate: dict[float, list[dict]] = defaultdict(list)
    for path in sorted(directory.glob("*.summary.json")):
        summary = json.loads(path.read_text(encoding="utf-8"))
        by_rate[summary["rate"]].append(summary)
    if not by_rate:
        raise FileNotFoundError(f"no *.summary.json files in {directory}")
    return dict(sorted(by_rate.items()))


def _point(runs: list[dict], metric: str) -> tuple[float, float, float]:
    """(mean, min, max) of a latency metric over the repetitions at one rate."""
    values = [run["latency_ms"][metric] for run in runs]
    return statistics.fmean(values), min(values), max(values)


def plot_metric(all_series: dict[str, Series], metric: str, ylabel: str, out_stem: Path) -> None:
    """One figure: `metric` (e.g. "p99") against request rate, one line per series."""
    fig, ax = plt.subplots(figsize=FIGSIZE)
    for label, series in all_series.items():
        rates = list(series)
        points = [_point(runs, metric) for runs in series.values()]
        means = [p[0] for p in points]
        whiskers = [[p[0] - p[1] for p in points], [p[2] - p[0] for p in points]]
        ax.errorbar(
            rates, means, yerr=whiskers, marker="o", markersize=3, capsize=2, linewidth=1, label=label
        )
    ax.set_xticks(sorted({rate for series in all_series.values() for rate in series}))  # the measured rates
    ax.set_xlabel("Keyword request rate (requests/s)")
    ax.set_ylabel(ylabel)
    ax.set_ylim(bottom=0)
    ax.grid(alpha=0.3)
    if len(all_series) > 1:
        ax.legend()
    fig.tight_layout()
    fig.savefig(out_stem.with_suffix(".pdf"))
    fig.savefig(out_stem.with_suffix(".png"), dpi=200)
    plt.close(fig)


def write_table(all_series: dict[str, Series], path: Path) -> None:
    """LaTeX rows (mean over repetitions) to paste into the report's results table."""
    lines = [
        r"\begin{tabular}{llrrrrr}",
        r"\hline",
        r"Setup & Rate (req/s) & Mean & p50 & p99 & Hit ratio & Errors \\",
        r"\hline",
    ]
    for label, series in all_series.items():
        for rate, runs in series.items():
            mean, p50, p99 = (_point(runs, m)[0] for m in ("mean", "p50", "p99"))
            hits = statistics.fmean(run["cache_hit_ratio"] for run in runs)
            errors = sum(run["errors"] for run in runs)
            hit_pct = rf"{hits * 100:.1f}\%"  # LaTeX needs % escaped
            lines.append(
                f"{label} & {rate:g} & {mean:.2f} & {p50:.2f} & {p99:.2f} & {hit_pct} & {errors} \\\\"
            )
    lines += [r"\hline", r"\end{tabular}"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _warn_about_bad_runs(all_series: dict[str, Series]) -> None:
    """Flag runs that should not go into the report as they are."""
    for label, series in all_series.items():
        for runs in series.values():
            for run in runs:
                problems = [
                    text
                    for bad, text in (
                        (not run["valid"], "load generator lagged"),
                        (run["errors"], f"{run['errors']} errors"),
                        (run["incorrect"], f"{run['incorrect']} incorrect counts"),
                    )
                    if bad
                ]
                if problems:
                    print(
                        f"WARNING {label} rate {run['rate']:g} rep {run['repetition']}: {', '.join(problems)}"
                    )


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: load the series, warn about bad runs, write both figures and the table."""
    parser = argparse.ArgumentParser(
        prog="python -m bench.plot", description="Plot latency vs. request rate."
    )
    parser.add_argument("--series", action="append", required=True, metavar="LABEL=DIR")
    parser.add_argument("--out", type=Path, default=Path("results/figures"))
    args = parser.parse_args(argv)

    all_series = {}
    for spec in args.series:
        label, _, directory = spec.partition("=")
        if not directory:
            parser.error(f"--series must look like LABEL=DIR, got {spec!r}")
        all_series[label] = load_series(Path(directory))

    args.out.mkdir(parents=True, exist_ok=True)
    _warn_about_bad_runs(all_series)
    plot_metric(all_series, "mean", "Mean latency (ms)", args.out / "latency_mean")
    plot_metric(all_series, "p99", "99th-percentile latency (ms)", args.out / "latency_p99")
    write_table(all_series, args.out / "latency_table.tex")
    print(f"figures and table written to {args.out}")


if __name__ == "__main__":
    main()
