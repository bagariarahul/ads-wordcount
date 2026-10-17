"""Tests. Run them in the client container: docker exec client python -m pytest tests"""

import asyncio
import threading
import time

import fakeredis
import rpyc
from rpyc.utils.server import ThreadedServer

from balancer import health, proxy
from balancer.algorithms import Server, least_connections, least_response_time
from wordcount.cache import Cache
from wordcount.server import WordCountService, tokenize

TEXTS = {"fable": tokenize("The whale met the Whale. THE whale's friend said: go, whale!")}


def make_cache():
    return Cache(client=fakeredis.FakeRedis(decode_responses=True))


def start_server(cache, name):
    server = ThreadedServer(WordCountService(TEXTS, cache, name), hostname="127.0.0.1", port=0)
    threading.Thread(target=server.start, daemon=True).start()
    while not server.active:
        time.sleep(0.01)
    return server


def start_balancer(servers):
    proxy.servers = servers
    loop = asyncio.new_event_loop()
    listener = loop.run_until_complete(asyncio.start_server(proxy.handle_client, "127.0.0.1", 0))
    threading.Thread(target=loop.run_forever, daemon=True).start()
    return listener.sockets[0].getsockname()[1]


def request(port):
    conn = rpyc.connect("127.0.0.1", port)
    try:
        return conn.root.count("fable", "whale")
    finally:
        conn.close()


def test_tokenize():
    assert tokenize("The Whale, the whale's!") == ["the", "whale", "the", "whale's"]


def test_count_and_cache():
    server = start_server(make_cache(), "s1")
    conn = rpyc.connect("127.0.0.1", server.port)
    assert conn.root.count("fable", "Whale") == (3, "s1", False)
    assert conn.root.count("fable", "whale") == (3, "s1", True)
    assert conn.root.hot_keywords(1) == (("whale", 2),)
    conn.close()
    server.close()


def test_least_connections():
    a, b = Server("a", "", 0), Server("b", "", 0)
    a.active = 2
    assert least_connections([a, b]) is b


def test_least_response_time():
    a, b = Server("a", "", 0), Server("b", "", 0)
    a.response_time, b.response_time = 1.0, 5.0
    a.last_used = b.last_used = time.monotonic()
    a.active = 1  # a: 2 * 1 = 2, b: 1 * 5 = 5
    assert least_response_time([a, b]) is a
    a.active = 5  # a: 6 * 1 = 6
    assert least_response_time([a, b]) is b


def test_balancer_spreads_requests():
    cache = make_cache()
    a, b = start_server(cache, "a"), start_server(cache, "b")
    port = start_balancer([Server("a", "127.0.0.1", a.port), Server("b", "127.0.0.1", b.port)])
    answered = [request(port)[1] for _ in range(4)]
    assert sorted(answered) == ["a", "a", "b", "b"]


def test_health_check():
    dead = Server("dead", "127.0.0.1", 1)  # nothing listens on port 1
    assert asyncio.run(health.is_alive(dead)) is False


def test_balancer_skips_dead_server():
    alive = start_server(make_cache(), "alive")
    dead = Server("dead", "127.0.0.1", 1)
    port = start_balancer([dead, Server("alive", "127.0.0.1", alive.port)])
    assert [request(port)[1] for _ in range(4)] == ["alive"] * 4
    assert not dead.healthy
