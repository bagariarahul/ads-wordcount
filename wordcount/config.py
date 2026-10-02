"""Settings for every role (server, client, tools), read from environment variables.

Why this module exists: docker-compose configures each container through
environment variables. Reading them in one place means each default is defined
exactly once, and a malformed value (e.g. WC_PORT=abc) stops the container at
startup with a clear message instead of failing later deep inside a request.

All variables share the WC_ prefix so they cannot collide with variables that the
base image or Docker itself sets.

Used by: server.main(), client.WordCountClient.from_env(), bench.loadgen.main(),
bench.trace.main().
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path

# RPyC's documentation uses 18861 for custom services (18812 is its "classic" mode).
DEFAULT_PORT = 18861
DEFAULT_CORPUS_DIR = "/data/corpus"  # where docker-compose.yml mounts the corpus


def _env_str(name: str, default: str) -> str:
    """Return environment variable `name`, or `default` if it is unset or blank."""
    value = os.environ.get(name, "").strip()
    return value or default


def _env_number(name: str, default: float, kind: type[int] | type[float]) -> int | float:
    """Return environment variable `name` parsed as `kind`, or `default` if unset.

    Why not int(os.environ[...]) at each call site: the error message here names
    the variable, which is what you need when a compose file has a typo.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return kind(raw)
    except ValueError:
        raise ValueError(f"environment variable {name} must be a {kind.__name__}, got {raw!r}") from None


def corpus_dir_from_env() -> Path:
    """Directory with the <text_id>.txt files: the server's corpus and the trace generator's input."""
    return Path(_env_str("WC_CORPUS_DIR", DEFAULT_CORPUS_DIR))


@dataclass(frozen=True)
class RedisSettings:
    """Where the cache lives. Frozen: settings never change after startup."""

    host: str
    port: int
    db: int

    @classmethod
    def from_env(cls) -> RedisSettings:
        """Read WC_REDIS_HOST, WC_REDIS_PORT and WC_REDIS_DB."""
        return cls(
            host=_env_str("WC_REDIS_HOST", "localhost"),
            port=int(_env_number("WC_REDIS_PORT", 6379, int)),
            db=int(_env_number("WC_REDIS_DB", 0, int)),
        )


@dataclass(frozen=True)
class ServerSettings:
    """Everything a server process needs.

    server_id is returned with every reply and printed in every log line. It is what
    tells the replicas apart in Phase 3, so it defaults to the container hostname
    (docker-compose sets `hostname: server1`, ...).
    """

    server_id: str
    bind_host: str
    port: int
    corpus_dir: Path
    redis: RedisSettings
    log_level: str

    @classmethod
    def from_env(cls) -> ServerSettings:
        """Read the server's WC_* variables (see the README's configuration table)."""
        return cls(
            server_id=_env_str("WC_SERVER_ID", socket.gethostname()),
            bind_host=_env_str("WC_BIND_HOST", "0.0.0.0"),  # all interfaces inside the container
            port=int(_env_number("WC_PORT", DEFAULT_PORT, int)),
            corpus_dir=corpus_dir_from_env(),
            redis=RedisSettings.from_env(),
            log_level=_env_str("WC_LOG_LEVEL", "INFO").upper(),
        )


@dataclass(frozen=True)
class ClientSettings:
    """Where a client sends requests: the server in Phase 2, the load balancer from Phase 3 on."""

    host: str
    port: int
    timeout_s: float

    @classmethod
    def from_env(cls) -> ClientSettings:
        """Read WC_HOST, WC_PORT and WC_TIMEOUT_S."""
        return cls(
            host=_env_str("WC_HOST", "localhost"),
            port=int(_env_number("WC_PORT", DEFAULT_PORT, int)),
            timeout_s=float(_env_number("WC_TIMEOUT_S", 10.0, float)),
        )
