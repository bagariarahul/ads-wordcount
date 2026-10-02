"""Everything that touches Redis: key names, commands, and behaviour when Redis is down.

Why a separate module: the server should say "look this up" or "remember this",
not "GET wc:count:...". Keeping Redis details here means a key-layout change
touches one file, and the cache logic is unit-tested against fakeredis without
starting a server (tests/test_cache.py).

Key layout. Every key starts with "wc:", so clear() can remove exactly our keys:

    wc:count:<text_id>:<word>   string       the cached count
    wc:hot                      sorted set   member = word, score = times it was requested

Neither text IDs (corpus.TEXT_ID_RE) nor normalized words (text.py) can contain
':', so two different requests can never map to the same key.

No expiry (TTL) on counts: the texts never change while the service runs, so a
cached count can never become wrong. Memory is bounded by Redis itself instead
(maxmemory + allkeys-lru in docker-compose.yml: when full, it evicts the least
recently used keys).

Network cost per request: a hit costs one Redis round trip (GET and ZINCRBY are
pipelined together); a miss costs two (that pipeline, then SET after counting).

Concurrency: two requests for the same uncached text and keyword that arrive
together both miss, both count and both store (a small "cache stampede"). That is
harmless here: the count is deterministic, so both store the same value, and it
can only happen until the first store lands. Preventing it would need a lock per
key in Redis, which costs an extra round trip on every miss.

Failure policy, fail-open: if Redis is unreachable, lookups behave as misses and
stores are skipped, so every request is still answered correctly, only slower.
The cache is an optimization; the count is the product. Two details make this
work in practice:
  * redis-py retries a failed command 10 times with exponential backoff by
    default, which costs seconds per call while Redis is down (measured: ~8 s
    per request). Retries are therefore off: one attempt per call.
  * after a failure, Redis is left alone for retry_after_s seconds (a simple
    circuit breaker), so an outage costs one failed attempt per window instead
    of one per request. Requests in that window are not counted as hot keywords.
The outage is logged once when it starts and once when it ends.

Used by: server.main() (connect), server.WordCountService (lookups, stores, hot
keywords), bench.loadgen (clear() between experiment runs), tests/test_cache.py.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import cast

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from wordcount.config import RedisSettings

log = logging.getLogger(__name__)

KEY_PREFIX = "wc:"
HOT_KEY = KEY_PREFIX + "hot"
RETRY_AFTER_S = 5.0  # how long Redis is left alone after a failed call


def count_key(text_id: str, word: str) -> str:
    """Redis key under which the count of `word` in `text_id` is cached."""
    return f"{KEY_PREFIX}count:{text_id}:{word}"


class CountCache:
    """Cache-aside store for counts plus the hot-keyword ranking.

    Thread safety: one instance is shared by all server threads. redis-py clients
    are thread-safe (each command borrows a pooled connection), pipelines are created
    per call, and the availability state is guarded by a lock.

    `clock` is injectable so tests can move time forward without sleeping.
    """

    def __init__(
        self,
        client: redis.Redis,
        retry_after_s: float = RETRY_AFTER_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._redis = client
        self._retry_after_s = retry_after_s
        self._clock = clock
        self._state_lock = threading.Lock()
        self._available = True
        self._skip_until = 0.0  # clock() value before which Redis is not contacted

    @classmethod
    def connect(cls, settings: RedisSettings, attempts: int = 30, delay_s: float = 1.0) -> CountCache:
        """Create a client and wait until Redis answers PING.

        Why wait: "container started" is not "Redis ready". docker-compose.yml already
        waits for Redis' health check; this loop also covers running a server by hand.
        """
        client = redis.Redis(
            host=settings.host,
            port=settings.port,
            db=settings.db,
            decode_responses=True,  # return str, not bytes
            socket_connect_timeout=2.0,
            socket_timeout=2.0,  # a stuck Redis must not stall a request for long
            retry=Retry(NoBackoff(), retries=0),  # fail fast; see "Failure policy" above
        )
        for attempt in range(1, attempts + 1):
            try:
                client.ping()
                return cls(client)
            except redis.RedisError as exc:
                if attempt == attempts:
                    raise
                log.info("waiting for Redis at %s:%d (%s)", settings.host, settings.port, exc)
                time.sleep(delay_s)
        raise AssertionError("unreachable")  # the loop either returns or raises

    def lookup_and_record(self, text_id: str, word: str) -> int | None:
        """Return the cached count (None on a miss) and count this request toward hot keywords.

        Both commands are needed on every request, so they share one pipeline: one
        network round trip instead of two. transaction=False because the two commands
        are independent; wrapping them in MULTI/EXEC would only add overhead.
        """
        if self._skipping():
            return None
        pipe = self._redis.pipeline(transaction=False)
        pipe.get(count_key(text_id, word))
        pipe.zincrby(HOT_KEY, 1, word)
        try:
            cached, _ = pipe.execute()
        except redis.RedisError as exc:
            self._record_failure(exc)
            return None
        self._record_success()
        return None if cached is None else int(cached)

    def store(self, text_id: str, word: str, count: int) -> None:
        """Cache a freshly computed count. Zero is cached too: "0" is a hit, only None is a miss."""
        if self._skipping():
            return
        try:
            self._redis.set(count_key(text_id, word), count)
        except redis.RedisError as exc:
            self._record_failure(exc)
        else:
            self._record_success()

    def hot_keywords(self, k: int) -> tuple[tuple[str, int], ...]:
        """The k most requested words with their request counts, most requested first.

        No fallback here: the request statistics only exist in Redis. RuntimeError is
        built in, so the client receives it as RuntimeError.
        """
        unavailable = RuntimeError("hot keywords unavailable: the cache is unreachable")
        if self._skipping():
            raise unavailable
        try:
            # redis-py types zrange() loosely; with decode_responses=True and withscores=True
            # it returns (member, score) pairs of str and float.
            rows = cast(
                "list[tuple[str, float]]",
                self._redis.zrange(HOT_KEY, 0, k - 1, desc=True, withscores=True),
            )
        except redis.RedisError as exc:
            self._record_failure(exc)
            raise unavailable from None
        self._record_success()
        return tuple((word, int(score)) for word, score in rows)

    def clear(self) -> int:
        """Delete every wc:* key and return how many were deleted.

        Benchmark harness only (bench.loadgen calls it before each run so every run
        starts from the same cold cache). The server never calls it and does not
        expose it to clients.
        """
        deleted = 0
        batch: list[str] = []
        for key in self._redis.scan_iter(match=KEY_PREFIX + "*", count=1000):
            batch.append(key)
            if len(batch) == 1000:
                deleted += self._redis.unlink(*batch)
                batch.clear()
        if batch:
            deleted += self._redis.unlink(*batch)
        return deleted

    def _skipping(self) -> bool:
        """True while a recent failure says to leave Redis alone (circuit open)."""
        with self._state_lock:
            return self._clock() < self._skip_until

    def _record_failure(self, error: Exception) -> None:
        """Open the circuit for retry_after_s; log only if Redis was considered up."""
        with self._state_lock:
            self._skip_until = self._clock() + self._retry_after_s
            was_available, self._available = self._available, False
        if was_available:
            log.warning("Redis unreachable (%s); answering without cache", error)

    def _record_success(self) -> None:
        """Close the circuit; log only if Redis had been considered down."""
        with self._state_lock:
            was_available, self._available = self._available, True
        if not was_available:
            log.info("Redis reachable again; caching resumed")
