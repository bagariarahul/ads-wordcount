"""Load balancer. Forwards every client connection to one of the servers, at byte level (no RPyC)."""

import asyncio
import logging
import os
import time

from balancer.algorithms import ALGORITHMS, Server, score

PORT = int(os.environ.get("PORT", 18861))
SERVERS = os.environ.get("SERVERS", "server1,server2,server3")
ALGORITHM = os.environ.get("LB_ALGORITHM", "least_connections")
CONNECT_TIMEOUT = 1.0

log = logging.getLogger("lb")
choose = ALGORITHMS[ALGORITHM]
servers = []
connections = 0


def parse_servers(value):
    # "server1,server2" or "host:port,host:port"
    result = []
    for entry in value.split(","):
        host, _, port = entry.strip().partition(":")
        result.append(Server(entry.strip(), host, int(port) if port else PORT))
    return result


def state():
    """What the algorithm looks at, for the log."""
    if ALGORITHM == "least_connections":
        return "open: " + " ".join(f"{s.name}={s.active}" for s in servers)
    return "score: " + " ".join(f"{s.name}={score(s):.2f}" for s in servers)


async def pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except ConnectionError:
        pass
    finally:
        try:
            if writer.can_write_eof():
                writer.write_eof()  # nothing more comes from this side
        except OSError:
            pass


async def handle_client(client_reader, client_writer):
    global connections
    connections += 1
    conn_id = connections
    try:
        info = state()
        server = choose(servers)
        server.active += 1
        server.total += 1
        server.last_used = time.monotonic()
        totals = " ".join(f"{s.name}={s.total}" for s in servers)
        log.info("#%d -> %s | %s | total: %s", conn_id, server.name, info, totals)

        try:
            server_reader, server_writer = await asyncio.wait_for(
                asyncio.open_connection(server.host, server.port), CONNECT_TIMEOUT)
        except (OSError, asyncio.TimeoutError) as e:
            server.active -= 1
            log.warning("#%d cannot connect to %s (%s)", conn_id, server.name, str(e) or "timeout")
            return

        start = time.monotonic()
        try:
            await asyncio.gather(pipe(client_reader, server_writer), pipe(server_reader, client_writer))
        finally:
            server_writer.close()
            server.active -= 1
            server.add_response_time((time.monotonic() - start) * 1000)
    finally:
        client_writer.close()


async def main():
    global servers
    logging.basicConfig(level=logging.INFO, format="%(asctime)s lb %(message)s", datefmt="%H:%M:%S")
    servers = parse_servers(SERVERS)
    listener = await asyncio.start_server(handle_client, port=PORT)
    log.info("listening on port %d, %s", PORT, ALGORITHM)
    async with listener:
        await listener.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
