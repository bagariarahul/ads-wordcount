"""Build the request trace that every experiment replays.

Why a fixed trace file instead of drawing random requests during a run:
  * Phase 3 compares "the same requests" on one server and on three servers;
  * every rate replays the same requests in the same order from a cold cache,
    so the cache-hit pattern is (almost) the same at every rate and essentially
    only the load differs (bench/loadgen.py explains the "almost").

Trace rows (CSV): seq, text_id, keyword, expected_count, gap
  gap is an inter-arrival time of a Poisson process at 1 request/s (exponential,
  mean 1). At rate R, bench.loadgen waits gap / R before each request, so every
  rate replays the same burst pattern, compressed in time. Poisson arrivals model
  many independent clients; a perfectly regular schedule would hide the queueing
  that bursts cause.

expected_count is computed with collections.Counter, a different code path from
the server's tuple.count, so the load generator can verify every reply (FR1).
Both still share text.tokenize: that is the definition of a word, not a detail.

Workload model:
  * texts are chosen uniformly at random;
  * keywords come from a pool of K alphabetic words drawn at random from the
    corpus's frequency ranks 100-5099 (skipping mostly function words like "the",
    and the very rare tail);
  * pool popularity follows a Zipf law: the word at popularity rank r is requested
    with probability proportional to 1/r^s. A few keywords dominate (the hot
    keywords, mostly cache hits); most are rare (mostly misses).

Run (client container):  python -m bench.trace --out traces/trace.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter
from dataclasses import astuple, dataclass, fields
from pathlib import Path

from wordcount.config import corpus_dir_from_env
from wordcount.corpus import TextCatalog

VOCAB_SKIP_TOP = 100  # most frequent words skipped (mostly function words)
VOCAB_SIZE = 5000  # frequency ranks the keyword pool is drawn from


@dataclass(frozen=True)
class TraceRow:
    """One request of the trace, with the count the server must return."""

    seq: int
    text_id: str
    keyword: str
    expected_count: int
    gap: float  # seconds until this request at 1 request/s


FIELDS = [f.name for f in fields(TraceRow)]


def build_trace(
    catalog: TextCatalog, requests: int, keywords: int, zipf_s: float, seed: int
) -> list[TraceRow]:
    """Generate `requests` trace rows; the same arguments always give the same trace."""
    rng = random.Random(seed)
    per_text = {text_id: Counter(catalog.get(text_id).words) for text_id in catalog.ids()}
    overall: Counter[str] = Counter()
    for counts in per_text.values():
        overall.update(counts)

    ranked = [word for word, _ in overall.most_common() if word.isalpha()]
    candidates = ranked[VOCAB_SKIP_TOP : VOCAB_SKIP_TOP + VOCAB_SIZE]
    if len(candidates) < keywords:
        raise ValueError(f"corpus offers only {len(candidates)} candidate keywords, asked for {keywords}")
    pool = rng.sample(candidates, keywords)  # random order = popularity order
    weights = [1 / rank**zipf_s for rank in range(1, keywords + 1)]

    text_ids = catalog.ids()
    keywords_drawn = rng.choices(pool, weights=weights, k=requests)
    texts_drawn = [rng.choice(text_ids) for _ in range(requests)]
    gaps = [rng.expovariate(1.0) for _ in range(requests)]
    # Rescale so the mean gap is exactly 1: the whole trace replayed at rate R then
    # offers exactly R requests/s on average (a raw sample is off by ~1/sqrt(N),
    # about 1% for 10,000 requests). Statistically this is a Poisson process
    # conditioned on its number of arrivals, so the burst structure is unchanged.
    scale = requests / sum(gaps)
    return [
        TraceRow(seq, text_id, keyword, per_text[text_id][keyword], gap * scale)
        for seq, (text_id, keyword, gap) in enumerate(zip(texts_drawn, keywords_drawn, gaps, strict=True))
    ]


def max_hit_ratio(rows: list[TraceRow]) -> float:
    """Best possible cache hit ratio from a cold start: every repeat of a (text, keyword) pair hits."""
    distinct = len({(row.text_id, row.keyword) for row in rows})
    return 1 - distinct / len(rows)


def write_trace(rows: list[TraceRow], path: Path) -> None:
    """Write rows as CSV (header = TraceRow field names); read back with read_trace()."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(FIELDS)
        writer.writerows(astuple(row) for row in rows)


def read_trace(path: Path) -> list[TraceRow]:
    """Parse a trace written by write_trace(). The one place that knows the CSV schema."""
    with path.open(newline="", encoding="utf-8") as fh:
        return [
            TraceRow(int(r["seq"]), r["text_id"], r["keyword"], int(r["expected_count"]), float(r["gap"]))
            for r in csv.DictReader(fh)
        ]


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: build the trace from the corpus and write it plus a .meta.json summary."""
    parser = argparse.ArgumentParser(prog="python -m bench.trace", description="Generate a request trace.")
    parser.add_argument("--corpus", type=Path, default=corpus_dir_from_env())
    parser.add_argument("--out", type=Path, default=Path("traces/trace.csv"))
    parser.add_argument("--requests", type=int, default=10_000)
    parser.add_argument("--keywords", type=int, default=1_000, help="size of the keyword pool")
    parser.add_argument("--zipf", type=float, default=1.0, help="Zipf exponent s of keyword popularity")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    catalog = TextCatalog.from_directory(args.corpus)
    rows = build_trace(catalog, args.requests, args.keywords, args.zipf, args.seed)
    write_trace(rows, args.out)
    meta = {
        "requests": args.requests,
        "keywords": args.keywords,
        "zipf_s": args.zipf,
        "seed": args.seed,
        "texts": list(catalog.ids()),
        "corpus_words": catalog.total_words(),
        "max_hit_ratio": round(max_hit_ratio(rows), 4),
    }
    args.out.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"wrote {len(rows)} requests to {args.out} (best possible hit ratio {meta['max_hit_ratio']:.1%})")


if __name__ == "__main__":
    main()
