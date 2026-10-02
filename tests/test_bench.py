"""The experiment harness: trace generation, statistics, a real load-generator run, plotting."""

import json
import random
import statistics
from collections import Counter

import numpy
import pytest

from bench import loadgen, plot, trace
from bench.stats import summarize
from bench.trace import TraceRow, build_trace, max_hit_ratio, read_trace, write_trace
from wordcount.corpus import TextCatalog


@pytest.fixture
def vocab_catalog(tmp_path, monkeypatch) -> TextCatalog:
    """Two texts over a 300-word alphabetic vocabulary with skewed frequencies."""
    monkeypatch.setattr(trace, "VOCAB_SKIP_TOP", 10)  # the real value assumes whole novels
    rng = random.Random(0)
    vocab = ["w" + "".join(rng.choice("abcdefghij") for _ in range(6)) for _ in range(300)]
    for text_id in ("left", "right"):
        words = rng.choices(vocab, weights=[1 / (i + 1) for i in range(300)], k=20_000)
        (tmp_path / f"{text_id}.txt").write_text(" ".join(words), encoding="utf-8")
    return TextCatalog.from_directory(tmp_path)


def test_trace_is_reproducible(vocab_catalog):
    first = build_trace(vocab_catalog, requests=500, keywords=50, zipf_s=1.0, seed=7)
    assert first == build_trace(vocab_catalog, requests=500, keywords=50, zipf_s=1.0, seed=7)
    assert first != build_trace(vocab_catalog, requests=500, keywords=50, zipf_s=1.0, seed=8)


def test_expected_counts_match_the_server_side_count(vocab_catalog):
    rows = build_trace(vocab_catalog, requests=300, keywords=50, zipf_s=1.0, seed=1)
    assert all(row.expected_count == vocab_catalog.count(row.text_id, row.keyword) for row in rows)


def test_popularity_is_skewed_and_gaps_are_poisson(vocab_catalog):
    rows = build_trace(vocab_catalog, requests=5_000, keywords=50, zipf_s=1.0, seed=3)
    top_share = Counter(row.keyword for row in rows).most_common(1)[0][1] / len(rows)
    assert top_share > 0.15  # rank 1 of Zipf(1) over 50 words gets ~22%
    assert statistics.fmean(row.gap for row in rows) == pytest.approx(1.0)  # rescaled to mean exactly 1
    assert statistics.stdev(row.gap for row in rows) == pytest.approx(1.0, abs=0.1)  # exponential: sd = mean
    assert 0 <= max_hit_ratio(rows) < 1


def test_trace_file_round_trip(tmp_path, vocab_catalog):
    rows = build_trace(vocab_catalog, requests=20, keywords=10, zipf_s=1.0, seed=2)
    write_trace(rows, tmp_path / "t.csv")
    assert read_trace(tmp_path / "t.csv") == rows


def test_summarize_matches_numpy():
    values = [random.Random(4).expovariate(1.0) for _ in range(1_000)]
    s = summarize(values)
    for name, q in (("p50", 50), ("p95", 95), ("p99", 99)):
        assert getattr(s, name) == pytest.approx(numpy.percentile(values, q))
    assert s.mean == pytest.approx(numpy.mean(values)) and s.max == max(values)


def test_load_generator_run(client, catalog, tmp_path):
    rows = [
        TraceRow(i, "fable", w, catalog.count("fable", w), 0.002) for i, w in enumerate(["whale", "the"] * 20)
    ]
    rows.append(TraceRow(40, "moby_dick", "whale", 0, 0.002))  # unknown text: must be recorded, not crash

    # workers=1: one request in flight at a time, so no two first requests for the same
    # keyword can miss concurrently (see wordcount/cache.py) and the hit pattern is exact.
    records = loadgen.run_once(client, rows, rate=1.0, workers=1)
    summary = loadgen.summarize_run(records, "test", 1.0, 1)
    loadgen.write_run(records, summary, tmp_path)

    assert [r.seq for r in records] == list(range(41))
    assert summary["errors"] == 1 and records[-1].error.startswith("LookupError")
    assert summary["incorrect"] == 0
    assert summary["served_by"] == {"test-server": 40}
    assert summary["cache_hit_ratio"] == pytest.approx(38 / 40)  # first request per keyword misses
    assert (tmp_path / "rate1_rep1.csv").read_text().count("\n") == 42  # header + 41 rows


def test_plot_writes_figures_and_table(tmp_path):
    runs = tmp_path / "phase2"
    runs.mkdir()
    for rate in (100, 200):
        summary = {
            "rate": rate,
            "repetition": 1,
            "valid": True,
            "errors": 0,
            "incorrect": 0,
            "cache_hit_ratio": 0.6,
            "latency_ms": {"n": 10, "mean": rate / 100, "p50": 1.0, "p95": 2.0, "p99": 3.0, "max": 4.0},
        }
        (runs / f"rate{rate}_rep1.summary.json").write_text(json.dumps(summary))
    plot.main(["--series", f"1 server={runs}", "--out", str(tmp_path / "fig")])
    produced = sorted(p.name for p in (tmp_path / "fig").iterdir())
    assert produced == [
        "latency_mean.pdf",
        "latency_mean.png",
        "latency_p99.pdf",
        "latency_p99.png",
        "latency_table.tex",
    ]
    assert r"1 server & 200 & 2.00" in (tmp_path / "fig" / "latency_table.tex").read_text()
