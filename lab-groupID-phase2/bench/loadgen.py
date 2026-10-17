"""Load generator: sends requests at fixed rates and writes the latency of every request to a CSV file.

    python -m bench.loadgen --label phase2 --rates 50 100 150 200 250 --repeat 3

Every run is written to results/<label>/rate<R>_<run>.csv.
"""

import argparse
import collections
import csv
import os
import random
import threading
import time

import numpy as np
import redis

from wordcount import client
from wordcount.server import tokenize

CORPUS_DIR = os.environ.get("CORPUS_DIR", "/data/corpus")
REDIS_HOST = os.environ.get("REDIS_HOST", "redis")


def make_requests(n, seed):
    """n random (text, keyword) pairs. The keywords are common words of the first text,
    without the 50 most common ones (the, and, of, ...)."""
    texts = sorted(f[:-4] for f in os.listdir(CORPUS_DIR) if f.endswith(".txt"))
    with open(os.path.join(CORPUS_DIR, texts[0] + ".txt"), encoding="utf-8") as f:
        common = collections.Counter(tokenize(f.read())).most_common(500)
    keywords = [word for word, _ in common[50:]]
    rng = random.Random(seed)
    return [(rng.choice(texts), rng.choice(keywords)) for _ in range(n)]


def run(requests, rate, path):
    """Sends every request from its own thread at its time, so a slow reply does not delay the next request."""
    rows = []
    lock = threading.Lock()
    threads = []

    def send(text, word, t):
        try:
            n, server, hit, latency = client.count(text, word)
            row = [t, latency, server, hit, ""]
        except Exception as e:
            row = [t, (time.perf_counter() - start - t) * 1000, "", False, type(e).__name__]
        with lock:
            rows.append(row)

    start = time.perf_counter()
    for i, (text, word) in enumerate(requests):
        t = i / rate
        time.sleep(max(0.0, start + t - time.perf_counter()))
        thread = threading.Thread(target=send, args=(text, word, t))
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()

    rows.sort()
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["time_s", "latency_ms", "server", "cache_hit", "error"])
        writer.writerows(rows)
    return rows


def summary(rows):
    ok = [r for r in rows if not r[4]]
    if not ok:
        return f"{len(rows)} requests, all failed"
    latencies = np.array([r[1] for r in ok])
    hits = sum(1 for r in ok if r[3])
    servers = collections.Counter(r[2] for r in ok)
    return (f"{len(rows)} requests, mean {latencies.mean():.2f} ms, p99 {np.percentile(latencies, 99):.2f} ms, "
            f"cache hits {hits / len(ok):.0%}, errors {len(rows) - len(ok)}, servers {dict(sorted(servers.items()))}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True, help="results go to results/<label>/")
    parser.add_argument("--rates", type=float, nargs="+", required=True, help="requests per second")
    parser.add_argument("--requests", type=int, default=5000, help="requests per run")
    parser.add_argument("--repeat", type=int, default=1, help="runs per rate")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    out_dir = os.path.join("results", args.label)
    os.makedirs(out_dir, exist_ok=True)
    requests = make_requests(args.requests, args.seed)
    cache = redis.Redis(host=REDIS_HOST)
    print(f"sending to {client.HOST}:{client.PORT}, {len(requests)} requests per run")

    for rate in args.rates:
        for k in range(1, args.repeat + 1):
            cache.flushdb()  # every run starts with an empty cache
            rows = run(requests, rate, os.path.join(out_dir, f"rate{rate:g}_{k}.csv"))
            print(f"rate {rate:g}/s run {k}: {summary(rows)}")


if __name__ == "__main__":
    main()
