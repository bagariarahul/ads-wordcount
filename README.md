# Word count service — ADS lab (2IMN10, Q1 2026–2027)

A client sends a text ID and a keyword; the server answers how often the keyword occurs in that text.
Answers are cached in Redis, which also tracks the most requested keywords. Python, RPyC, Redis,
Docker Compose.

Status: **Phase 1 and Phase 2 done** (one server). Phases 3 (three replicas + load balancer) and 4
(health checks, failover) build on this code base. Report text for Phases 1–2 is in `report/`.

## Quick start

Needs Docker Desktop. Python on the host is only used once, to download the corpus.

```bash
python scripts/fetch_corpus.py                 # 8 Project Gutenberg books -> corpus/
#   no Python on the host?  docker run --rm -v "${PWD}:/w" -w /w python:3.12-slim python scripts/fetch_corpus.py

docker compose --profile tools build           # builds both images (server "runtime", client "dev")
docker compose run --rm --no-deps client       # runs the test suite (50 tests)

docker compose up -d                           # starts redis + server1 (the client has its own profile)
docker compose ps                              # server1 should show (healthy)
docker compose run --rm client python -m wordcount.client texts
docker compose run --rm client python -m wordcount.client count moby_dick Whale
docker compose run --rm client python -m wordcount.client hot -k 5
docker compose logs -f server1                 # one line per request: text, word, count, hit/miss, ms
docker compose down
```

Code is baked into the images: after changing code, run `docker compose --profile tools build` again.
No ports are published to the host; all containers talk over the Compose network.

## Phase 2 experiment: exact procedure

Run everything on one machine and do not run other heavy programs meanwhile.

1. **Record the environment** for the report (Table II): CPU model, cores, RAM, OS, and what Docker
   may use: `docker version --format "{{.Server.Version}}"` and
   `docker info --format "{{.NCPU}} CPUs, {{.MemTotal}} bytes"`.
2. **Generate the trace once** and commit it, so every phase replays the same requests:
   `docker compose run --rm --no-deps client python -m bench.trace`
   (10,000 requests, 1,000-keyword pool, Zipf s = 1.0, seed 42 → `traces/trace.csv` and
   `traces/trace.meta.json`, which holds the best possible hit ratio for the report).
3. **Calibrate**: find where one server saturates.
   `docker compose run --rm client python -m bench.loadgen --label calibration --requests 2000 --rates 100 200 400 800 1200`
   Saturation shows as p99 jumping by roughly 10×, the run taking longer than requests/rate, or the
   run being flagged `INVALID` (load generator lagging). Call that rate R_sat.
4. **Pick five rates**, evenly spaced from about 15 % to 85–90 % of R_sat, lowest ≥ 10 req/s and steps
   ≥ 10 (e.g. R_sat ≈ 600 → 100 200 300 400 500).
5. **Run** (three repetitions per rate, each from an empty cache; about 10–15 min in total):
   `docker compose run --rm client python -m bench.loadgen --label phase2 --repetitions 3 --rates R1 R2 R3 R4 R5`
6. **Plot**:
   `docker compose run --rm --no-deps client python -m bench.plot --series "1 server=results/phase2" --out results/figures`
   Warnings are printed for runs with errors, incorrect counts or a lagging generator: do not report
   those runs, re-run them.
7. **Fill in the report**: copy `results/figures/latency_mean.pdf`, `latency_p99.pdf` and
   `latency_table.tex` to `report/figures/`, then replace every **[bold bracketed]** placeholder in
   `report/phase2.tex` (numbers from `results/phase2/*.summary.json`, `traces/trace.meta.json`, and
   the server's startup log line `loaded 8 texts (N words)`).
8. **Evidence for the round-trip claim** in the report:
   `docker compose run --rm client python -m bench.rpc_messages moby_dick whale`

`report/main_preview.tex` compiles Phases 1–2 in the IEEE conference class to check layout and page
use (they currently take about one of the three text pages). Paste `phase1.tex`/`phase2.tex` into the
group's own main file; `figures/architecture_phase2.tex` is the TikZ source of Fig. 1.

## Architecture (Phase 2)

Client–server in three tiers, each a container on one Compose network (Fig. 1 in the report):

```
client ──RPyC/TCP, one connection per request──▶ server1 ──RESP/TCP──▶ redis
(CLI, load generator)                            ▲ texts read once at startup
                                                 corpus volume (read-only)
```

What happens on one request (`wordcount/server.py`, `WordCountService.exposed_count`):

1. the client opens a connection and calls `count(text_id, keyword)`;
2. the server checks the argument types, normalizes the keyword (exactly one word) and checks that
   the text exists — before touching Redis, so bad requests never become hot keywords;
3. one pipelined Redis round trip: GET the cached count and ZINCRBY the keyword's request counter;
4. on a miss: count by scanning the text's words, then SET the result (zero counts are cached too);
5. reply `(count, server_id, cache_hit)`, log one line, the client closes the connection.

## Code map

| Module | Responsibility | Used by |
|---|---|---|
| `wordcount/config.py` | read all `WC_*` environment variables; defaults defined once | server, client, load generator |
| `wordcount/text.py` | the definition of a word: `tokenize`, `normalize_keyword` | corpus, server, trace |
| `wordcount/corpus.py` | `TextCatalog`: load texts by ID at startup, count in them | server, trace |
| `wordcount/cache.py` | `CountCache`: Redis keys and commands, hot keywords, fail-open | server, load generator (`clear`) |
| `wordcount/server.py` | RPyC service: validation, cache-aside flow; process entry point | container `server1` |
| `wordcount/client.py` | `WordCountClient` (timed requests) and the CLI | CLI, load generator |
| `bench/trace.py` | build/read/write the fixed request trace with expected counts | load generator |
| `bench/loadgen.py` | open-loop replay at given rates; per-request CSV + run summary | experiments |
| `bench/stats.py` | mean and percentiles (one formula for tables and figures) | load generator |
| `bench/plot.py` | summaries → figures (PDF/PNG) and LaTeX table | report |
| `bench/rpc_messages.py` | diagnostic: RPyC messages behind one request | report evidence |
| `scripts/fetch_corpus.py` | download the books, strip Gutenberg's licence text | setup |
| `tests/` | 50 tests; the server tests use real RPyC sockets with an in-memory fake Redis | `pytest` |

### Where each concern lives (check here before writing a new function)

| Concern | The one place | Not duplicated in |
|---|---|---|
| What a word is | `text.tokenize`, `text.normalize_keyword` | server, trace and tests all call these |
| Redis key names and commands | `cache.count_key`, `cache.HOT_KEY`, `CountCache` | server and harness never build keys |
| Reading environment variables | `config.*Settings.from_env` | nowhere else reads `os.environ` |
| Measuring request latency | `WordCountClient.count` → `CountReply.elapsed_ms` | the load generator only records it |
| Error text of a failed request | `client.error_message` | CLI and load generator share it |
| Percentiles | `bench.stats.summarize` | plot reads summaries, never recomputes |
| Trace CSV format | `bench.trace.read_trace` / `write_trace` | |
| Server container settings | `x-server` anchor in `docker-compose.yml` | Phase 3 replicas reuse it |

## Design decisions (and the debrief questions they answer)

**Why not Elasticsearch for the texts?** The assignment names Redis as the cache and says texts are
"stored on the server"; Elasticsearch is not mentioned. If Elasticsearch did the counting, it would
replace the server that Phase 2 measures. If it only stored texts, every replica would depend on one
shared JVM node (≈1 GB heap) that the Phase 3 scaling experiment could not scale, and its analyzers
would change what counts as a word. Files on a read-only volume are simpler and literally "on the
server".

**Why are texts referenced by ID and loaded at startup?** The ID (file name) is looked up in a
dictionary built at startup, so the server never opens a path that came from a client, and a missing
corpus stops the server at startup instead of failing requests.

**Why scan on every miss instead of precomputing word counts?** A precomputed table makes every
count O(1) and leaves the Redis cache, which the assignment requires, with nothing to save. The scan
(`tuple.count` in C over the interned words) is the cost the cache amortizes.

**Why one connection per request, and what does it cost?** The Phase 3 load balancer forwards raw
bytes, so it can only pick a server when a connection opens; per-request balancing therefore needs
per-request connections, and Phase 2 uses the same policy so results are comparable. Cost, measured
with `bench.rpc_messages`: a TCP handshake plus four RPyC round trips before the reply (GETROOT,
INSPECT, GETATTR, CALL) and one more to close (CLOSE waits for an answer, after the reply). A reused
connection needs only GETATTR and CALL.

**Why are replies plain tuples?** RPyC copies only `str`, `int`, `float`, `bool`, `None`, `bytes`
and *exact* `tuple`/`frozenset` by value. A list, dict or even a namedtuple arrives as a netref (a
proxy); every access is another round trip to the server that created it — which in Phase 3 may not be
the server the next connection reaches. `tests/test_service.py` checks this.

**Why only built-in exception types?** RPyC rebuilds built-in exceptions on the client
(`ValueError`, `LookupError`, `TypeError`, `RuntimeError`), so callers catch them normally. Custom
exception classes arrive as a generic RPyC exception unless both sides opt into importing them.

**Why does the client not use `rpyc.connect()`?** It retries a connect timeout up to 6 times × 3 s
and leaves Nagle's algorithm on. The client connects once with its own timeout and `TCP_NODELAY`, and
resolves the host name once instead of asking Docker's DNS on every request.

**Why cache-aside, without TTL, with zeros cached?** Texts never change, so a cached count is never
stale; Redis' `maxmemory` + `allkeys-lru` bounds memory instead. A zero is a valid answer: caching
it avoids rescanning for absent words (only `None` means "not cached").

**What happens when Redis is down?** Requests are still answered correctly, just without the cache
(fail-open); `hot -k` returns an error. redis-py's default of 10 retries with backoff made each request
take ~8 s during an outage, so retries are off, and after a failure Redis is left alone for 5 s (a
small circuit breaker). The outage is logged once when it starts and once when it ends.

**Why open-loop, Poisson arrivals, a fixed trace and a cold cache?** Open-loop: a client that waits
for each reply slows down with the server and hides the queueing the p99 figure should show.
Poisson: independent users arrive in bursts. Fixed trace and cold cache: every rate (and later every
phase) sees the same requests with the same hit pattern, so only the load differs. Every reply is
checked against counts computed independently; runs where the generator lagged are flagged.

**Why does p99 grow faster than the mean?** One server on one CPU executes request code one thread
at a time (Python's GIL). Bursts queue behind slow requests (misses on the largest books). For such a
queue the waiting time grows with λ·E[S²]/(1−ρ) (Pollaczek–Khinchine): the mix of fast hits and slow
misses makes E[S²] large, so waiting — and the tail first — rises well before the CPU is saturated.

**Why is the server limited to 1 CPU?** Results then do not depend on the host's core count, and
Phase 3 (3 replicas × 1 CPU) compares fairly with Phase 2 (1 × 1 CPU).

**Why `python:3.12-slim` instead of the assignment's `python:3.9-slim-buster`?** Buster is
end-of-life; its repositories moved to archive.debian.org, so `apt-get update` in that image fails.

**Why does the server handle SIGTERM?** It runs as PID 1, and Linux gives PID 1 no default signal
actions: without a handler, `docker stop` waits 10 s and then kills it. With the handler it stops in
well under a second and logs `SIGTERM received` — useful evidence in Phase 4.

**Known limitations.** Two concurrent first requests for the same text and keyword can both miss
(a small cache stampede; harmless because both store the same count). Requests made during a Redis
outage are not counted as hot keywords. `ThreadedServer` starts one thread per connection; with one
connection per request that is one thread per request.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `WC_SERVER_ID` | container hostname | name in replies and log lines |
| `WC_BIND_HOST` | `0.0.0.0` | interface the server listens on |
| `WC_PORT` | `18861` | server port (client: target port) |
| `WC_CORPUS_DIR` | `/data/corpus` | directory with `<text_id>.txt` files |
| `WC_REDIS_HOST` / `_PORT` / `_DB` | `localhost` / `6379` / `0` | Redis location |
| `WC_LOG_LEVEL` | `INFO` | `WARNING` hides the per-request lines |
| `WC_HOST` | `localhost` | where the client sends requests (server now, load balancer in Phase 3) |
| `WC_TIMEOUT_S` | `10` | client connect and reply timeout |

## Coding conventions

* Every module starts with a docstring stating its single responsibility and who uses it.
* Every non-trivial function says what it does and **why it exists**; shared logic says where else it
  is used. Before adding a function, check "Where each concern lives" — extend the owner instead of
  writing a second version.
* Configuration only through `WC_*` variables, parsed in `config.py`.
* Errors that cross the RPC boundary use built-in exception types.
* Dependencies are pinned (`requirements*.txt`) to the versions the tests ran against.
* Checks: `python -m pytest`, `ruff check .`, `ruff format --check .`,
  `mypy wordcount bench scripts --ignore-missing-imports` (all clean).

## Submission

Tag each phase and let git build the zip (ignored files such as the downloaded corpus stay out):

```bash
git tag phase2
git archive --format=zip --prefix=lab-GROUPID-phase2/ -o lab-GROUPID-phase2.zip phase2
```

## Troubleshooting

* `server1` never becomes healthy: `docker compose logs server1`. An empty `corpus/` stops it with
  `no *.txt files in /data/corpus`.
* A run is marked `INVALID`: the load generator fell behind its schedule; give Docker more CPUs or
  lower the rate. Do not report that run.
* `bench.plot` warns about errors or incorrect counts: investigate before reporting; correct runs have
  zero of both.
