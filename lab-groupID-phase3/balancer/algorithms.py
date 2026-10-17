"""Least connections and least response time."""

import time


class Server:
    def __init__(self, name, host, port):
        self.name = name
        self.host = host
        self.port = port
        self.active = 0            # open connections
        self.total = 0             # connections sent to this server so far
        self.response_time = None  # average connection time in ms
        self.last_used = 0.0

    def add_response_time(self, ms):
        if self.response_time is None:
            self.response_time = ms
        else:
            self.response_time = 0.9 * self.response_time + 0.1 * ms


def least_connections(servers):
    # on a tie the server that was used longest ago, so idle servers take turns
    return min(servers, key=lambda s: (s.active, s.last_used))


def recent_response_time(server):
    # a server that was not used for a second counts as 0, so it gets measured again;
    # otherwise a server that was slow once would never be chosen again
    if server.response_time is None or time.monotonic() - server.last_used > 1.0:
        return 0.0
    return server.response_time


def score(server):
    return (server.active + 1) * recent_response_time(server)


def least_response_time(servers):
    return min(servers, key=lambda s: (score(s), s.last_used))


ALGORITHMS = {
    "least_connections": least_connections,
    "least_response_time": least_response_time,
}
