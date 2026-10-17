"""Redis cache for the counts and the hot keywords."""

import time

import redis


class Cache:
    def __init__(self, host="redis", port=6379, client=None):
        # the tests pass a fakeredis client
        self.r = client or redis.Redis(host=host, port=port, decode_responses=True)

    def wait_until_ready(self):
        while True:
            try:
                self.r.ping()
                return
            except redis.exceptions.ConnectionError:
                time.sleep(1)

    def get_count(self, text_id, word):
        value = self.r.get(f"count:{text_id}:{word}")
        return None if value is None else int(value)

    def set_count(self, text_id, word, count):
        self.r.set(f"count:{text_id}:{word}", count)

    def add_request(self, word):
        self.r.zincrby("hot", 1, word)

    def hot_keywords(self, k=10):
        top = self.r.zrevrange("hot", 0, k - 1, withscores=True)
        # a tuple is sent to the client by value, a list would arrive as a remote reference
        return tuple((word, int(score)) for word, score in top)
