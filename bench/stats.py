"""Latency statistics, defined once for the whole harness.

Why one module: the numbers in the report's table and the points in its figures
must come from the same formula. bench.loadgen summarizes each run with
summarize(); bench.plot only reads those summaries, it never recomputes them.

Percentiles use linear interpolation between closest ranks
(statistics.quantiles with method="inclusive"; identical to numpy.percentile's
default). The report states this in its experiment setup.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class LatencySummary:
    """Distribution of one run's latencies, in milliseconds."""

    n: int
    mean: float
    p50: float
    p95: float
    p99: float
    max: float

    def as_dict(self) -> dict[str, float]:
        """Plain dict for the JSON run summaries."""
        return asdict(self)


def summarize(values_ms: Sequence[float]) -> LatencySummary:
    """Mean, median, 95th and 99th percentile, and maximum of `values_ms`.

    Needs at least two values (a percentile of one sample is meaningless).
    """
    if len(values_ms) < 2:
        raise ValueError("need at least two latency samples")
    cuts = statistics.quantiles(values_ms, n=100, method="inclusive")  # cuts[i] = (i+1)-th percentile
    return LatencySummary(
        n=len(values_ms),
        mean=statistics.fmean(values_ms),
        p50=cuts[49],
        p95=cuts[94],
        p99=cuts[98],
        max=max(values_ms),
    )
