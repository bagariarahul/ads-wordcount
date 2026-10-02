"""Client side of the word count service: a small API plus a command-line tool.

Connection policy: one new TCP + RPyC connection per request, closed right after
the reply. RPyC is connection-oriented; every call made on one connection goes to
whichever server accepted it. From Phase 3 on, the load balancer forwards raw
bytes and can only pick a server when a connection opens, so per-request
balancing requires per-request connections. Phase 2 uses the same policy so that
its latencies are directly comparable with Phase 3's.

Measured cost of that policy (bench/rpc_messages.py prints it): besides the TCP
handshake, one count() on a fresh connection takes four RPyC round trips before
the reply arrives: GETROOT (fetch the service object), INSPECT (learn its
methods), GETATTR ("count"), CALL. Closing then takes a fifth (CLOSE waits for
the server's answer); it happens after the reply, so it is not part of the
measured latency, but it keeps the connection open a little longer. On a reused
connection only GETATTR and CALL remain.

Used by: the CLI below (manual tests, demos) and bench.loadgen (experiments).

CLI examples (inside the client container):
    python -m wordcount.client texts
    python -m wordcount.client count moby_dick Whale
    python -m wordcount.client hot -k 5
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from dataclasses import dataclass

import rpyc
from rpyc.core.stream import SocketStream

from wordcount.config import ClientSettings


@dataclass(frozen=True)
class CountReply:
    """Result of one count request.

    elapsed_ms is the latency exactly as the assignment defines it: from sending the
    request (opening the connection is part of sending it) to receiving the reply,
    measured on the client. Closing the connection afterwards is not included.
    Same idea as `requests.Response.elapsed`.
    """

    count: int
    server_id: str
    cache_hit: bool
    elapsed_ms: float


class WordCountClient:
    """Talks to one address: the server (Phase 2) or the load balancer (Phase 3+).

    Safe to share between threads: it holds no connection, only the address.
    """

    def __init__(self, host: str, port: int, timeout_s: float = 10.0) -> None:
        self.host = host
        self.port = port
        self.timeout_s = timeout_s
        # Resolve the name once. Resolving per request would add a lookup at Docker's
        # embedded DNS server to every measured latency; the target keeps its address
        # for the lifetime of an experiment run. IPv4 only: Docker's default networks
        # are IPv4, and rpyc's SocketStream expects an IPv4 address unless told otherwise.
        self._address = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]

    @classmethod
    def from_env(cls) -> WordCountClient:
        """Client for the address in WC_HOST / WC_PORT (see config.ClientSettings)."""
        settings = ClientSettings.from_env()
        return cls(settings.host, settings.port, settings.timeout_s)

    def count(self, text_id: str, keyword: str) -> CountReply:
        """Ask how often `keyword` occurs in text `text_id`.

        Raises ValueError (bad keyword), LookupError (unknown text), TypeError, or
        OSError / TimeoutError when the target cannot be reached.
        """
        started = time.perf_counter()
        with self._connect() as conn:
            count, server_id, cache_hit = conn.root.count(text_id, keyword)
            elapsed_ms = (time.perf_counter() - started) * 1000
        return CountReply(count, server_id, cache_hit, elapsed_ms)

    def hot_keywords(self, k: int = 10) -> tuple[tuple[str, int], ...]:
        """The k most requested keywords and how often each was requested."""
        with self._connect() as conn:
            return conn.root.hot_keywords(k)

    def list_texts(self) -> tuple[str, ...]:
        """IDs of the texts the service can count in."""
        with self._connect() as conn:
            return conn.root.list_texts()

    def _connect(self) -> rpyc.Connection:
        """Open one connection with our own timeout policy.

        Why not plain rpyc.connect(): it gives no control over two things that matter
        here. (1) On a connect timeout it retries up to 6 times with a 3 s timeout
        each, so an unreachable server could stall a request for ~18 s; we try once,
        bounded by timeout_s. (2) It leaves Nagle's algorithm on; TCP_NODELAY sends
        each small RPC message immediately, as latency-sensitive RPC clients usually do.
        """
        stream = SocketStream.connect(
            self._address, self.port, timeout=self.timeout_s, attempts=1, nodelay=True
        )
        return rpyc.connect_stream(stream, config={"sync_request_timeout": self.timeout_s})


def error_message(error: BaseException) -> str:
    """The message of a failed request, without the server-side traceback RPyC appends.

    Used by the CLI below and by bench.loadgen (the error column of its results).
    """
    text = str(error).strip()
    return text.splitlines()[0] if text else type(error).__name__


def main(argv: list[str] | None = None) -> int:
    """CLI entry point (subcommands count, hot, texts); returns the process exit code."""
    parser = argparse.ArgumentParser(prog="python -m wordcount.client", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    count_cmd = commands.add_parser("count", help="count a keyword in a text")
    count_cmd.add_argument("text_id")
    count_cmd.add_argument("keyword")
    hot_cmd = commands.add_parser("hot", help="show the most requested keywords")
    hot_cmd.add_argument("-k", type=int, default=10)
    commands.add_parser("texts", help="list the available text IDs")
    args = parser.parse_args(argv)

    try:
        client = WordCountClient.from_env()
        if args.command == "count":
            reply = client.count(args.text_id, args.keyword)
            print(
                f"{args.keyword!r} occurs {reply.count} times in {args.text_id} "
                f"(served by {reply.server_id}, cache {'hit' if reply.cache_hit else 'miss'}, "
                f"{reply.elapsed_ms:.2f} ms)"
            )
        elif args.command == "hot":
            for rank, (word, times) in enumerate(client.hot_keywords(args.k), start=1):
                print(f"{rank:>3}. {word:<20} {times} requests")
        else:
            print("\n".join(client.list_texts()))
    except (ValueError, LookupError, TypeError, RuntimeError, OSError) as error:
        print(f"error: {error_message(error)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
