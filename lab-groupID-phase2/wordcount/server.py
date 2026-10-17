"""RPyC word count server. The texts in CORPUS_DIR are loaded at startup, the text id is the file name."""

import logging
import os
import re
import socket
import time

import rpyc
from rpyc.utils.server import ThreadedServer

from wordcount.cache import Cache

CORPUS_DIR = os.environ.get("CORPUS_DIR", "/data/corpus")
PORT = int(os.environ.get("PORT", 18861))
SERVER_ID = os.environ.get("SERVER_ID", socket.gethostname())
REDIS_HOST = os.environ.get("REDIS_HOST", "redis")

WORD = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)*")  # also words like don't and whale's
log = logging.getLogger("server")


def tokenize(text):
    return WORD.findall(text.lower())


def load_texts(directory):
    texts = {}
    for name in sorted(os.listdir(directory)):
        if name.endswith(".txt"):
            with open(os.path.join(directory, name), encoding="utf-8") as f:
                texts[name[:-4]] = tokenize(f.read())
    return texts


class WordCountService(rpyc.Service):
    def __init__(self, texts, cache, server_id=SERVER_ID):
        self.texts = texts
        self.cache = cache
        self.server_id = server_id

    def exposed_count(self, text_id, keyword):
        """Returns (count, server id, cache hit)."""
        start = time.perf_counter()
        if text_id not in self.texts:
            raise ValueError(f"unknown text: {text_id}")
        words = tokenize(keyword)
        if len(words) != 1:
            raise ValueError(f"keyword must be one word: {keyword}")
        word = words[0]

        self.cache.add_request(word)
        count = self.cache.get_count(text_id, word)
        hit = count is not None
        if not hit:
            count = self.texts[text_id].count(word)
            self.cache.set_count(text_id, word, count)

        ms = (time.perf_counter() - start) * 1000
        log.info("count %s %s = %d (%s, %.2f ms)", text_id, word, count, "hit" if hit else "miss", ms)
        return count, self.server_id, hit

    def exposed_hot_keywords(self, k=10):
        return self.cache.hot_keywords(k)


def main():
    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s {SERVER_ID} %(message)s", datefmt="%H:%M:%S")
    texts = load_texts(CORPUS_DIR)
    if not texts:
        raise SystemExit(f"no .txt files in {CORPUS_DIR}, run scripts/fetch_corpus.py first")
    words = sum(len(t) for t in texts.values())
    log.info("loaded %d texts (%d words): %s", len(texts), words, ", ".join(texts))
    cache = Cache(REDIS_HOST)
    cache.wait_until_ready()

    # rpyc logs every connection, too much with one connection per request
    rpyc_log = logging.getLogger("rpyc")
    rpyc_log.setLevel(logging.WARNING)
    server = ThreadedServer(WordCountService(texts, cache), port=PORT, logger=rpyc_log)
    log.info("ready on port %d", PORT)
    server.start()


if __name__ == "__main__":
    main()
