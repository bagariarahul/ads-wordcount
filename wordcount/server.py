"""The RPyC word count server: service definition and process entry point.

Remote API (a client calls these as conn.root.<name>(...)):

    count(text_id, keyword) -> (count, server_id, cache_hit)
    hot_keywords(k=10)      -> ((word, times_requested), ...)
    list_texts()            -> (text_id, ...)

Why every reply is a plain tuple of str/int/bool: RPyC sends exactly those types
by value. A list, dict or namedtuple is sent as a remote reference ("netref"):
the client gets a proxy, and every access to it is one more round trip back to
the server that created it. That is slow, and from Phase 3 on it would break,
because the load balancer may send the next connection to a different replica.

Why count() also returns server_id and cache_hit: the load generator records them
per request, so the report can show which replica served each request (Phase 3)
and the cache hit ratio of every run, without scraping logs.

Run with:  python -m wordcount.server   (configuration: see wordcount/config.py)
"""

from __future__ import annotations

import logging
import signal
import time

import rpyc
from rpyc.utils.server import ThreadedServer

from wordcount.cache import CountCache
from wordcount.config import ServerSettings
from wordcount.corpus import TextCatalog
from wordcount.text import normalize_keyword

log = logging.getLogger("wordcount.server")

MAX_HOT_KEYWORDS = 100


def _require_str(name: str, value: object) -> str:
    """Reject anything that is not a real str before it reaches the logic.

    `type(...) is str` rather than isinstance(): RPyC passes a genuine str by value,
    so anything else (e.g. a remote proxy object) means a misbehaving client.
    TypeError is built in, so the client receives it as TypeError.
    """
    if type(value) is not str:
        raise TypeError(f"{name} must be a str, got {type(value).__name__}")
    return value


class WordCountService(rpyc.Service):
    """Validates requests and combines the text catalog with the Redis cache.

    One instance is shared by all connection threads (see main()); that is safe
    because the catalog is read-only and CountCache is thread-safe.
    """

    def __init__(self, catalog: TextCatalog, cache: CountCache, server_id: str) -> None:
        super().__init__()
        self._catalog = catalog
        self._cache = cache
        self._server_id = server_id

    def exposed_count(self, text_id: str, keyword: str) -> tuple[int, str, bool]:
        """Occurrences of `keyword` in text `text_id`, served cache-aside.

        Order matters: the request is fully validated (keyword shape, text exists)
        before Redis is touched, so a bad request never pollutes the hot keywords.
        Errors reach the client as ValueError (bad keyword), LookupError (unknown
        text) or TypeError (wrong argument type).
        """
        started = time.perf_counter()
        text_id = _require_str("text_id", text_id)
        word = normalize_keyword(_require_str("keyword", keyword))
        self._catalog.get(text_id)  # raises LookupError for unknown IDs

        cached = self._cache.lookup_and_record(text_id, word)
        cache_hit = cached is not None
        if cached is None:
            count = self._catalog.count(text_id, word)
            self._cache.store(text_id, word, count)
        else:
            count = cached

        log.info(
            "count text=%s word=%s -> %d (%s, %.2f ms)",
            text_id,
            word,
            count,
            "hit" if cache_hit else "miss",
            (time.perf_counter() - started) * 1000,
        )
        return (count, self._server_id, cache_hit)

    def exposed_hot_keywords(self, k: int = 10) -> tuple[tuple[str, int], ...]:
        """The k (1..100) most requested keywords over all texts, most requested first."""
        if type(k) is not int or not 1 <= k <= MAX_HOT_KEYWORDS:
            raise ValueError(f"k must be an int between 1 and {MAX_HOT_KEYWORDS}")
        return self._cache.hot_keywords(k)

    def exposed_list_texts(self) -> tuple[str, ...]:
        """IDs of all texts this server can count in."""
        return self._catalog.ids()


def _quiet_rpyc_logger() -> logging.Logger:
    """Logger for RPyC's own server messages, limited to warnings.

    RPyC logs three INFO lines per connection (accepted / welcome / goodbye). With
    one connection per request that would triple the log volume and bury the
    request lines that the report's screenshots need.
    """
    rpyc_log = logging.getLogger("rpyc.server")
    rpyc_log.setLevel(logging.WARNING)
    return rpyc_log


def _close_on_sigterm(server: ThreadedServer) -> None:
    """Make `docker stop` shut the server down immediately and log it.

    The server runs as PID 1 in its container, and Linux applies no default signal
    action to PID 1: without this handler SIGTERM is ignored, and `docker stop`
    waits 10 s before sending SIGKILL. Closing the server makes server.start() return.
    """

    def handle(signum: int, frame: object) -> None:
        """Signal handler: log, then stop accepting connections."""
        log.info("SIGTERM received, shutting down")
        server.close()

    signal.signal(signal.SIGTERM, handle)


def main() -> None:
    """Process entry point: load the corpus, connect to Redis, serve until SIGTERM."""
    settings = ServerSettings.from_env()
    logging.basicConfig(
        level=settings.log_level,
        format=f"%(asctime)s {settings.server_id} %(levelname)s %(message)s",
    )

    started = time.perf_counter()
    catalog = TextCatalog.from_directory(settings.corpus_dir)
    log.info(
        "loaded %d texts (%d words) in %.1f s: %s",
        len(catalog.ids()),
        catalog.total_words(),
        time.perf_counter() - started,
        ", ".join(catalog.ids()),
    )
    cache = CountCache.connect(settings.redis)

    server = ThreadedServer(
        WordCountService(catalog, cache, settings.server_id),
        hostname=settings.bind_host,
        port=settings.port,
        logger=_quiet_rpyc_logger(),
    )
    _close_on_sigterm(server)
    log.info("ready on %s:%d", settings.bind_host, settings.port)
    server.start()  # blocks; one thread per connection
    log.info("stopped")


if __name__ == "__main__":
    main()
