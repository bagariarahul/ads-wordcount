"""CountCache (wordcount/cache.py) against fakeredis, including a Redis outage."""

import logging

import fakeredis
import pytest

from wordcount.cache import HOT_KEY, CountCache, count_key


def test_miss_then_hit(cache):
    assert cache.lookup_and_record("fable", "whale") is None
    cache.store("fable", "whale", 4)
    assert cache.lookup_and_record("fable", "whale") == 4


def test_zero_is_a_hit_not_a_miss(cache):
    cache.store("fable", "absent", 0)
    assert cache.lookup_and_record("fable", "absent") == 0


def test_every_request_counts_toward_hot_keywords(cache):
    for word, times in (("whale", 3), ("sea", 1), ("ship", 2)):
        for _ in range(times):
            cache.lookup_and_record("fable", word)  # misses count too
    assert cache.hot_keywords(2) == (("whale", 3), ("ship", 2))


def test_hot_keywords_are_counted_across_texts(cache):
    cache.lookup_and_record("fable", "whale")
    cache.lookup_and_record("note", "whale")
    assert cache.hot_keywords(1) == (("whale", 2),)


def test_clear_removes_only_our_keys(cache, redis_client):
    redis_client.set("someone-else", "1")
    cache.store("fable", "whale", 4)
    cache.lookup_and_record("fable", "whale")
    assert cache.clear() == 2  # the count key and the hot-keyword set
    assert redis_client.get("someone-else") == "1"
    assert redis_client.get(count_key("fable", "whale")) is None
    assert not redis_client.exists(HOT_KEY)


def test_fail_open_when_redis_is_down(caplog):
    server = fakeredis.FakeServer()
    # retry_after_s=0: try Redis on every call, so each call exercises the failure path
    cache = CountCache(fakeredis.FakeRedis(server=server, decode_responses=True), retry_after_s=0)
    server.connected = False

    with caplog.at_level(logging.INFO, logger="wordcount.cache"):
        assert cache.lookup_and_record("fable", "whale") is None  # behaves as a miss
        cache.store("fable", "whale", 4)  # skipped, no exception
        assert cache.lookup_and_record("fable", "whale") is None
        with pytest.raises(RuntimeError, match="unreachable"):
            cache.hot_keywords(3)
        server.connected = True
        cache.store("fable", "whale", 4)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1  # logged when the outage starts, not per request
    assert "reachable again" in caplog.records[-1].getMessage()
    assert cache.lookup_and_record("fable", "whale") == 4


def test_after_a_failure_redis_is_left_alone_for_a_while():
    server = fakeredis.FakeServer()
    redis_client = fakeredis.FakeRedis(server=server, decode_responses=True)
    now = [100.0]
    cache = CountCache(redis_client, retry_after_s=5, clock=lambda: now[0])

    server.connected = False
    assert cache.lookup_and_record("fable", "whale") is None  # fails: circuit opens for 5 s
    server.connected = True
    now[0] += 4.9
    assert cache.lookup_and_record("fable", "whale") is None  # skipped without contacting Redis
    assert redis_client.zcard(HOT_KEY) == 0
    now[0] += 0.1
    assert cache.lookup_and_record("fable", "whale") is None  # retried: a genuine miss
    assert redis_client.zcard(HOT_KEY) == 1
