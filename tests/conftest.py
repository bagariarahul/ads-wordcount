"""Shared fixtures: a tiny corpus, an in-memory fake Redis, and a real RPyC server.

The server fixture runs the actual WordCountService in a ThreadedServer on a free
port and talks to it over real TCP sockets, so tests exercise the same RPyC
serialization, exception transport and connection handling as production. Only
Redis is replaced (fakeredis implements the same commands in memory).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import fakeredis
import pytest
from rpyc.utils.server import ThreadedServer

from wordcount.cache import CountCache
from wordcount.client import WordCountClient
from wordcount.corpus import TextCatalog
from wordcount.server import WordCountService

# Small texts with the tricky cases in them: case, punctuation, curly apostrophes,
# possessives, hyphens, underscores and digits.
TEXTS = {
    "fable": (
        "The whale met the Whale. THE whale's friend said: don\u2019t go, whale! Sea-captain whale_shark 42"
    ),
    "note": "Nothing about whales here, just one whale.",
}


@pytest.fixture
def corpus_dir(tmp_path: Path) -> Path:
    for text_id, body in TEXTS.items():
        (tmp_path / f"{text_id}.txt").write_text(body, encoding="utf-8")
    return tmp_path


@pytest.fixture
def catalog(corpus_dir: Path) -> TextCatalog:
    return TextCatalog.from_directory(corpus_dir)


@pytest.fixture
def redis_client() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def cache(redis_client: fakeredis.FakeRedis) -> CountCache:
    return CountCache(redis_client)


@pytest.fixture
def server(catalog: TextCatalog, cache: CountCache) -> Iterator[ThreadedServer]:
    quiet = logging.getLogger("tests.rpyc")
    quiet.setLevel(logging.WARNING)
    srv = ThreadedServer(
        WordCountService(catalog, cache, "test-server"), hostname="127.0.0.1", port=0, logger=quiet
    )
    threading.Thread(target=srv.start, daemon=True).start()
    deadline = time.monotonic() + 5
    while not srv.active:  # start() is listening once `active` is set
        if time.monotonic() > deadline:
            raise RuntimeError("test server did not start")
        time.sleep(0.01)
    yield srv
    srv.close()


@pytest.fixture
def client(server: ThreadedServer) -> WordCountClient:
    return WordCountClient("127.0.0.1", server.port, timeout_s=5)
