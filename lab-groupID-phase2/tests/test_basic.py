"""Tests. Run them in the client container: docker exec client python -m pytest tests"""

import threading
import time

import fakeredis
import rpyc
from rpyc.utils.server import ThreadedServer

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
