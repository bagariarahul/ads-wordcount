"""Figures for the report from the CSV files of loadgen.py.

    python -m bench.plot latency results/phase2 --out results/figures/phase2
"""

import argparse
import collections
import csv
import glob
import os
import re

import matplotlib.pyplot as plt
import numpy as np


def read_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def latency_per_rate(directory):
    """{rate: (mean, p99)}, averaged over the runs of each rate. Failed requests are left out."""
    runs = collections.defaultdict(list)
    for path in glob.glob(os.path.join(directory, "rate*_*.csv")):
        rate = float(re.search(r"rate([\d.]+)_", os.path.basename(path)).group(1))
        latencies = np.array([float(r["latency_ms"]) for r in read_csv(path) if not r["error"]])
        runs[rate].append((latencies.mean(), np.percentile(latencies, 99)))
    return {rate: np.mean(values, axis=0) for rate, values in sorted(runs.items())}


def plot_latency(directories, labels, out):
    data = [latency_per_rate(d) for d in directories]
    for label, values in zip(labels, data):
        print(label)
        for rate, (mean, p99) in values.items():
            print(f"  {rate:g} req/s: mean {mean:.2f} ms, p99 {p99:.2f} ms")

    for index, name, title in [(0, "mean", "Average latency"), (1, "p99", "99th-percentile latency")]:
        plt.figure(figsize=(4.5, 3))
        for label, values in zip(labels, data):
            plt.plot(list(values), [v[index] for v in values.values()], marker="o", label=label)
        plt.xticks(list(data[0]))
        plt.ylim(bottom=0)
        plt.xlabel("Keyword requests per second")
        plt.ylabel("Latency (ms)")
        plt.title(title)
        plt.grid(alpha=0.3)
        if len(data) > 1:
            plt.legend()
        plt.tight_layout()
        plt.savefig(f"{out}_{name}.png", dpi=200)
        plt.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=["latency"])
    parser.add_argument("inputs", nargs="+", help="result directories")
    parser.add_argument("--labels", nargs="+", help="one legend label per directory")
    parser.add_argument("--out", required=True, help="file name without extension")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    plot_latency(args.inputs, args.labels or args.inputs, args.out)


if __name__ == "__main__":
    main()
