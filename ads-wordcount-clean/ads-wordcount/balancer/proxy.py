"""Load balancer. Forwards every client connection to one of the servers, at byte level (no RPyC).

With HEALTH_CHECKS=on, servers that are down are skipped. If the load balancer cannot connect
to the chosen server, it marks it down and tries another server for the same client.
"""

import asyncio
import logging
import os
import time

from balancer import health
from balancer.algorithms import ALGORITHMS, Server, score

PORT = int(os.environ.get("PORT", 18861))
SERVERS = os.environ.get("SERVERS", "server1,server2,server3")
ALGORITHM = os.environ.get("LB_ALGORITHM", "least_connections")
HEALTH_CHECKS = os.environ.get("HEALTH_CHECKS", "on") == "on"
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
    parts = []
    for s in servers:
        if not s.healthy:
            parts.append(f"{s.name}=DOWN")
        elif ALGORITHM == "least_connections":
            parts.append(f"{s.name}={s.active}")
        else:
            parts.append(f"{s.name}={score(s):.2f}")
    name = "open" if ALGORITHM == "least_connections" else "score"
    return f"{name}: " + " ".join(parts)


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
    tried = []
    try:
        while True:
            candidates = [s for s in servers if s.healthy and s not in tried]
            if not candidates:
                # no healthy server left, try the others anyway
                candidates = [s for s in servers if s not in tried]
            if not candidates:
                log.warning("#%d no server reachable", conn_id)
                return

            info = state()
            server = choose(candidates)
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
                if not HEALTH_CHECKS:
                    return
                health.set_status(server, False)
                tried.append(server)
                continue

            start = time.monotonic()
            try:
                await asyncio.gather(pipe(client_reader, server_writer), pipe(server_reader, client_writer))
            finally:
                server_writer.close()
                server.active -= 1
                server.add_response_time((time.monotonic() - start) * 1000)
            return
    finally:
        client_writer.close()


async def main():
    global servers
    logging.basicConfig(level=logging.INFO, format="%(asctime)s lb %(message)s", datefmt="%H:%M:%S")
    servers = parse_servers(SERVERS)
    listener = await asyncio.start_server(handle_client, port=PORT)
    log.info("listening on port %d, %s, health checks %s", PORT, ALGORITHM, "on" if HEALTH_CHECKS else "off")
    async with listener:
        if HEALTH_CHECKS:
            await asyncio.gather(listener.serve_forever(), health.monitor(servers))
        else:
            await listener.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
