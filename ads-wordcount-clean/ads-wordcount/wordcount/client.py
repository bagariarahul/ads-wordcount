"""Client. Every request uses a new connection, so the load balancer can pick a server for every request.

    python -m wordcount.client <text> <keyword>
    python -m wordcount.client hot [k]
"""

import os
import sys
import time

import rpyc

HOST = os.environ.get("HOST", "lb")
PORT = int(os.environ.get("PORT", 18861))


def count(text_id, keyword):
    """Returns (count, server id, cache hit, latency in ms)."""
    start = time.perf_counter()
    conn = rpyc.connect(HOST, PORT)
    try:
        n, server_id, hit = conn.root.count(text_id, keyword)
        latency = (time.perf_counter() - start) * 1000
    finally:
        conn.close()
    return n, server_id, hit, latency


def hot_keywords(k=10):
    conn = rpyc.connect(HOST, PORT)
    try:
        return conn.root.hot_keywords(k)
    finally:
        conn.close()


def main(args):
    if args and args[0] == "hot":
        for word, requests in hot_keywords(int(args[1]) if len(args) > 1 else 10):
            print(f"{word:20} {requests}")
    elif len(args) == 2:
        n, server_id, hit, latency = count(args[0], args[1])
        print(f"'{args[1]}' occurs {n} times in {args[0]} "
              f"(server {server_id}, cache {'hit' if hit else 'miss'}, {latency:.2f} ms)")
    else:
        print("usage: python -m wordcount.client <text> <keyword> | hot [k]")


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except ValueError as e:
        print("error:", str(e).splitlines()[0])  # rpyc adds the server's traceback after the message
