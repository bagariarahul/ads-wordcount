"""Health checks: every 2 seconds try to open a TCP connection to every server."""

import asyncio
import logging

INTERVAL = 2.0
TIMEOUT = 1.0

log = logging.getLogger("lb")


async def is_alive(server):
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(server.host, server.port), TIMEOUT)
        writer.close()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


def set_status(server, healthy):
    if server.healthy != healthy:
        server.healthy = healthy
        log.warning("%s is %s", server.name, "UP" if healthy else "DOWN")


async def monitor(servers):
    while True:
        results = await asyncio.gather(*(is_alive(s) for s in servers))
        for server, alive in zip(servers, results):
            set_status(server, alive)
        await asyncio.sleep(INTERVAL)
