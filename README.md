# Word Count Service

This project implements the word-count service for Phases 1 and 2 of the ADS
assignment. A client sends a text identifier and a keyword, and the service
returns the number of occurrences of that keyword in the selected text.

## Current implementation

The Phase 2 system contains:

- one Python word-count server, running as `server1`;
- one Redis instance used for cached counts and request statistics;
- a client and benchmark tools;
- Docker Compose configuration for running the components together.

The server loads the corpus when it starts. A request is validated, the keyword
is normalized, and the cache is checked before the text is scanned. Cache misses
are stored in Redis for later requests. The response contains the count, the
server identifier, and whether the result came from the cache.

The Phase 2 architecture is:

```text
client  ── RPyC/TCP ──>  server1  ── Redis protocol ──>  redis
                           |
                           └── read-only corpus volume
```

Each request uses a new client connection. The server is limited to one CPU so
that the single-server latency measurements are reproducible.

## Running the system

The corpus is generated from the included download script:

```bash
python scripts/fetch_corpus.py
```

Build the application and development images:

```bash
docker compose --profile tools build
```

Start the Phase 2 services:

```bash
docker compose up -d
docker compose ps
```

The expected services are `redis` and the healthy `server1` container. The
client can then be used for simple requests:

```bash
docker compose run --rm client python -m wordcount.client texts
docker compose run --rm client python -m wordcount.client count moby_dick Whale
docker compose run --rm client python -m wordcount.client hot -k 5
```

Stop the services with:

```bash
docker compose down
```

## Evaluation

The project includes tests for tokenization, corpus loading, caching, service
requests, and benchmark statistics. They can be run in the development
container:

```bash
docker compose --profile tools run --rm --no-deps client
```

The benchmark tools in `bench/` generate a fixed request trace, replay it at
different request rates, collect latency measurements, and create the figures
used in the Phase 2 report. The report source and architecture figure are in
`report/`.
