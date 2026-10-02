"""Open-loop load generator: replays a trace at fixed request rates and records every request.

Why open-loop: each request is sent at its scheduled time whether or not earlier
requests have been answered, like independent users would. A closed-loop client
(send, wait for the reply, send the next) slows down exactly when the server
does, so it cannot hold a target rate and hides the queueing delay that the
p99 figure is supposed to show.

Why threads: RPyC calls block, so concurrency comes from a thread pool; each
request runs on a pool thread with its own connection (see wordcount.client).

Validity check: send_lag = actual send time - scheduled send time. If the pool or
the dispatcher cannot keep up, lag grows and the run measures the load generator,
not the server. Runs whose p99 lag exceeds MAX_VALID_LAG_MS are marked invalid.

Every run starts from a cold cache (all wc:* keys deleted first), so the same
trace produces (almost) the same hits and misses at every rate. "Almost": under
load, two concurrent first requests for the same text and keyword can both miss
(see wordcount/cache.py), so each run's summary reports its actual hit ratio.
Clearing the cache is the only reason the load generator talks to Redis; the
service itself never lets clients do so.

Outputs per run, in <out>/<label>/:
    rate<R>_rep<k>.csv           one row per request (Record)
    rate<R>_rep<k>.summary.json  the run's statistics (read by bench.plot)

Run (client container):
    python -m bench.loadgen --trace traces/trace.csv --label phase2 --rates 100 200 300 400 500
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from bench.stats import summarize
from bench.trace import TraceRow, read_trace
from wordcount.cache import CountCache
from wordcount.client import WordCountClient, error_message
from wordcount.config import RedisSettings

MAX_VALID_LAG_MS = 10.0  # p99 send lag above this means the generator, not the server, was the bottleneck


@dataclass(frozen=True)
class Record:
    """What happened to one request. Times are relative to the start of the run."""

    seq: int
    text_id: str
    keyword: str
    expected_count: int
    count: int | None  # None if the request failed
    correct: bool
    served_by: str
    cache_hit: bool | None
    scheduled_ms: float
    send_lag_ms: float
    latency_ms: float
    finished_ms: float
    error: str


RECORD_FIELDS = [f.name for f in fields(Record)]


def _send(client: WordCountClient, row: TraceRow, scheduled: float, run_start: float) -> Record:
    """Send one request (on a pool thread) and turn the outcome into a Record.

    A failed request is a measurement, not a crash: every exception is recorded
    as data so a run with errors still completes and reports them.
    """
    begun = time.perf_counter()
    try:
        reply = client.count(row.text_id, row.keyword)
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
        count, served_by, cache_hit = None, "", None
        latency_ms, error = (
            (time.perf_counter() - begun) * 1000,
            f"{type(exc).__name__}: {error_message(exc)}",
        )
    else:
        count, served_by, cache_hit = reply.count, reply.server_id, reply.cache_hit
        latency_ms, error = reply.elapsed_ms, ""
    return Record(
        seq=row.seq,
        text_id=row.text_id,
        keyword=row.keyword,
        expected_count=row.expected_count,
        count=count,
        correct=count == row.expected_count,
        served_by=served_by,
        cache_hit=cache_hit,
        scheduled_ms=(scheduled - run_start) * 1000,
        send_lag_ms=(begun - scheduled) * 1000,
        latency_ms=latency_ms,
        finished_ms=(time.perf_counter() - run_start) * 1000,
        error=error,
    )


def run_once(client: WordCountClient, rows: Sequence[TraceRow], rate: float, workers: int) -> list[Record]:
    """Replay `rows` at `rate` requests/s (open loop) and return one Record per request, in trace order."""
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="request") as pool:
        run_start = time.perf_counter() + 0.05  # small head start: the first request is not late
        due = 0.0
        futures = []
        for row in rows:
            due += row.gap / rate
            scheduled = run_start + due
            wait = scheduled - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
            futures.append(pool.submit(_send, client, row, scheduled, run_start))
        return [future.result() for future in futures]


def summarize_run(records: Sequence[Record], label: str, rate: float, repetition: int) -> dict:
    """Statistics of one run, as written to the .summary.json file.

    Latency percentiles cover successful requests only; failures are counted
    separately so they cannot hide as fast or slow samples.
    """
    ok = [r for r in records if not r.error]
    duration_s = max(r.finished_ms for r in records) / 1000
    lag = summarize([r.send_lag_ms for r in records])
    return {
        "label": label,
        "rate": rate,
        "repetition": repetition,
        "requests": len(records),
        "errors": len(records) - len(ok),
        "incorrect": sum(1 for r in ok if not r.correct),
        "cache_hit_ratio": sum(1 for r in ok if r.cache_hit) / len(ok) if ok else 0.0,
        "achieved_rate": len(records) / duration_s,
        "latency_ms": summarize([r.latency_ms for r in ok]).as_dict() if len(ok) >= 2 else None,
        "send_lag_ms": {"p99": lag.p99, "max": lag.max},
        "valid": lag.p99 <= MAX_VALID_LAG_MS,
        "served_by": dict(Counter(r.served_by for r in ok)),
    }


def write_run(records: Sequence[Record], summary: dict, directory: Path) -> Path:
    """Write the per-request CSV and the summary JSON; return the CSV path."""
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"rate{summary['rate']:g}_rep{summary['repetition']}"
    csv_path = directory / f"{stem}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=RECORD_FIELDS)
        writer.writeheader()
        writer.writerows(asdict(r) for r in records)
    (directory / f"{stem}.summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return csv_path


def _describe(summary: dict) -> str:
    """One console line per run."""
    lat = summary["latency_ms"] or {"mean": float("nan"), "p99": float("nan")}
    flag = "" if summary["valid"] else "  INVALID: load generator lagging, see send_lag_ms"
    return (
        f"rate {summary['rate']:g}/s rep {summary['repetition']}: {summary['requests']} requests, "
        f"mean {lat['mean']:.2f} ms, p99 {lat['p99']:.2f} ms, hits {summary['cache_hit_ratio']:.1%}, "
        f"errors {summary['errors']}, incorrect {summary['incorrect']}, "
        f"lag p99 {summary['send_lag_ms']['p99']:.2f} ms{flag}"
    )


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: replay the trace at every rate x repetition and write the results."""
    parser = argparse.ArgumentParser(
        prog="python -m bench.loadgen", description="Replay a trace at fixed rates."
    )
    parser.add_argument("--trace", type=Path, default=Path("traces/trace.csv"))
    parser.add_argument("--label", required=True, help="experiment name, e.g. phase2 (results subdirectory)")
    parser.add_argument("--rates", type=float, nargs="+", required=True, help="requests per second")
    parser.add_argument("--requests", type=int, default=None, help="replay only the first N trace rows")
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--workers", type=int, default=256, help="max requests in flight")
    parser.add_argument("--keep-cache", action="store_true", help="do not clear the cache before each run")
    parser.add_argument("--out", type=Path, default=Path("results"))
    args = parser.parse_args(argv)

    rows = read_trace(args.trace)[: args.requests]
    client = WordCountClient.from_env()
    cache = None if args.keep_cache else CountCache.connect(RedisSettings.from_env())
    print(f"target {client.host}:{client.port}, {len(rows)} requests per run")

    for rate in args.rates:
        for repetition in range(1, args.repetitions + 1):
            if cache is not None:
                cache.clear()
            records = run_once(client, rows, rate, args.workers)
            summary = summarize_run(records, args.label, rate, repetition)
            write_run(records, summary, args.out / args.label)
            print(_describe(summary), flush=True)


if __name__ == "__main__":
    main()
