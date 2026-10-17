# Word count service - ADS lab

A client asks how often a keyword occurs in a text stored on the server. The server counts it and
caches the result in Redis, which also keeps track of the most requested keywords (hot keywords).
Three servers run behind a load balancer (least connections or least response time) that checks
the health of the servers.

## Files

```
wordcount/server.py      RPyC server: loads the texts, counts, uses the cache
wordcount/cache.py       Redis: cached counts and hot keywords
wordcount/client.py      client, one request per connection, measures the latency
balancer/proxy.py        load balancer, forwards the bytes of every connection to a server
balancer/algorithms.py   least connections and least response time
balancer/health.py       health checks
bench/loadgen.py         sends requests at fixed rates, one CSV file per run
bench/plot.py            figures for the report
scripts/fetch_corpus.py  downloads the books into corpus/
tests/test_basic.py      tests
```

## Setup

```
python scripts/fetch_corpus.py
docker compose up -d --build
docker exec client python -m pytest tests
```

Without Python on the host, download the books with
`docker run --rm -v "${PWD}:/w" -w /w python:3.12-slim python scripts/fetch_corpus.py`.

## Using it

```
docker exec client python -m wordcount.client moby_dick whale
docker exec client python -m wordcount.client hot 5
docker compose logs -f lb
docker compose logs -f server1
```

The load balancer settings are in `.env`:

- `LB_ALGORITHM`: `least_connections` or `least_response_time`
- `HEALTH_CHECKS`: `on`, or `off` (then it behaves like the load balancer of Phase 3)

After changing them: `docker compose up -d lb`.

## Experiments

Results are written to `results/` on the host. The plot script prints the values that are in the
figures.

Phase 2, one server without load balancer, 5 rates with 3 runs each:

```
docker exec -e HOST=server1 client python -m bench.loadgen --label phase2 --rates 50 100 150 200 250 --repeat 3
docker exec client python -m bench.plot latency results/phase2 --out results/figures/phase2
```

Phase 3, the same requests through the load balancer, once per algorithm (`LB_ALGORITHM` in `.env`):

```
docker exec client python -m bench.loadgen --label phase3-lc --rates 50 100 150 200 250 --repeat 3
docker exec client python -m bench.loadgen --label phase3-lrt --rates 50 100 150 200 250 --repeat 3
docker exec client python -m bench.plot latency results/phase2 results/phase3-lc results/phase3-lrt --labels "1 server" "3 servers, LC" "3 servers, LRT" --out results/figures/phase3
```

Phase 4: a 60 s run at 100 requests/s. In a second terminal stop server2 when the load generator
prints `20 s` and start it again at `40 s`:

```
docker exec client python -m bench.loadgen --label failover-lc --rates 100 --requests 6000 --progress
docker stop server2
docker ps -a
docker start server2
docker exec client python -m bench.plot timeline results/failover-lc/rate100_1.csv --stop 20 --start 40 --out results/figures/failover-lc
```

Do this four times: least connections and least response time, with `HEALTH_CHECKS=off` and `on`.
