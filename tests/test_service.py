"""The service over real RPyC connections (server fixture in conftest.py)."""

import socket
import time

import pytest
import rpyc

from wordcount.cache import HOT_KEY
from wordcount.client import WordCountClient


def test_count_is_computed_then_served_from_cache(client):
    first = client.count("fable", "Whale!")
    second = client.count("fable", "whale")
    assert (first.count, first.server_id, first.cache_hit) == (4, "test-server", False)
    assert (second.count, second.cache_hit) == (4, True)
    assert first.elapsed_ms > 0


def test_replies_arrive_by_value_not_as_remote_proxies(server):
    """Plain tuples come back as real tuples; a list would arrive as a netref."""
    conn = rpyc.connect("127.0.0.1", server.port)
    try:
        conn.root.count("fable", "whale")
        replies = [conn.root.count("fable", "whale"), conn.root.hot_keywords(1), conn.root.list_texts()]
    finally:
        conn.close()
    assert [type(r) for r in replies] == [tuple, tuple, tuple]
    assert type(replies[1][0]) is tuple


@pytest.mark.parametrize(
    ("text_id", "keyword", "error"),
    [("fable", "new york", ValueError), ("moby_dick", "whale", LookupError), ("fable", 123, TypeError)],
)
def test_errors_arrive_as_builtin_exceptions(client, text_id, keyword, error):
    with pytest.raises(error):
        client.count(text_id, keyword)


def test_rejected_requests_do_not_count_as_hot(client, redis_client):
    for text_id, keyword in (("fable", "new york"), ("moby_dick", "whale")):
        with pytest.raises((ValueError, LookupError)):
            client.count(text_id, keyword)
    assert redis_client.zcard(HOT_KEY) == 0


def test_hot_keywords_over_rpc(client):
    for keyword in ("whale", "whale", "the"):
        client.count("fable", keyword)
    assert client.hot_keywords(2) == (("whale", 2), ("the", 1))


@pytest.mark.parametrize("k", [0, 101])
def test_hot_keywords_rejects_bad_k(client, k):
    with pytest.raises(ValueError):
        client.hot_keywords(k)


def test_list_texts(client):
    assert client.list_texts() == ("fable", "note")


def test_unreachable_server_fails_fast():
    with socket.socket() as probe:  # grab a free port, then release it: nobody listens there
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    started = time.perf_counter()
    with pytest.raises(OSError):
        WordCountClient("127.0.0.1", port, timeout_s=1).count("fable", "whale")
    assert time.perf_counter() - started < 1
