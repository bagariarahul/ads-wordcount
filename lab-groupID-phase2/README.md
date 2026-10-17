# Word count service - ADS lab

A client asks how often a keyword occurs in a text stored on the server. The server counts it and
caches the result in Redis, which also keeps track of the most requested keywords (hot keywords).

## Files

```
wordcount/server.py      RPyC server: loads the texts, counts, uses the cache
wordcount/cache.py       Redis: cached counts and hot keywords
wordcount/client.py      client, one request per connection, measures the latency
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
docker compose logs -f server1
```

## Experiments

Results are written to `results/` on the host. The plot script prints the values that are in the
figures.

5 rates with 3 runs each:

```
docker exec client python -m bench.loadgen --label phase2 --rates 50 100 150 200 250 --repeat 3
docker exec client python -m bench.plot latency results/phase2 --out results/figures/phase2
```
