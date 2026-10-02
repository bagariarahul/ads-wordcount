"""Diagnostic: print the RPyC protocol messages that one count() request generates.

Why it exists: the report explains Phase 2 latency partly by the number of network
round trips per request (see wordcount/client.py). This makes that claim
reproducible against the running server instead of taking it on faith.

It wraps two internal methods of rpyc's Connection, so it is tied to the rpyc
version pinned in requirements.txt. It is a diagnostic, not part of the service.

Run (client container, server running):  python -m bench.rpc_messages moby_dick whale
"""

from __future__ import annotations

import argparse
import threading

import rpyc
from rpyc.core import consts, protocol

from wordcount.client import WordCountClient

_HANDLER_NAMES = {v: k.removeprefix("HANDLE_") for k, v in vars(consts).items() if k.startswith("HANDLE_")}


def trace_messages(action) -> list[str]:
    """Run `action()` and return the requests this process sent, marked sync or async.

    A sync request waits for a reply (one network round trip); an async one does not.
    """
    events: list[str] = []
    inside_sync = threading.local()
    original_sync, original_send = protocol.Connection.sync_request, protocol.Connection._send

    def sync_request(self, handler, *args):
        """Mark requests sent from inside a synchronous call."""
        inside_sync.active = True
        try:
            return original_sync(self, handler, *args)
        finally:
            inside_sync.active = False

    def send(self, msg, seq, args):
        """Record every outgoing request before sending it as usual."""
        if msg == consts.MSG_REQUEST:
            kind = "round trip" if getattr(inside_sync, "active", False) else "async"
            events.append(f"{_HANDLER_NAMES.get(args[0], args[0])} ({kind})")
        return original_send(self, msg, seq, args)

    protocol.Connection.sync_request, protocol.Connection._send = sync_request, send
    try:
        action()
    finally:
        protocol.Connection.sync_request, protocol.Connection._send = original_sync, original_send
    return events


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: print the messages for a fresh connection and for a reused one."""
    parser = argparse.ArgumentParser(prog="python -m bench.rpc_messages")
    parser.add_argument("text_id")
    parser.add_argument("keyword")
    args = parser.parse_args(argv)

    client = WordCountClient.from_env()
    print("fresh connection per request (what the service uses):")
    print("  " + ", ".join(trace_messages(lambda: client.count(args.text_id, args.keyword))))

    conn = rpyc.connect(client.host, client.port)
    conn.root.count(args.text_id, args.keyword)  # warm up: root and method info now cached
    print("second call on a reused connection (for comparison):")
    print("  " + ", ".join(trace_messages(lambda: conn.root.count(args.text_id, args.keyword))))
    conn.close()


if __name__ == "__main__":
    main()
