"""Word count service for the ADS lab (TU/e 2IMN10, Q1 2026-2027).

A client sends a text ID and a keyword; a server answers how often the keyword
occurs in that text, caching every answer in Redis and tracking which keywords
are requested most (hot keywords).

One responsibility per module, and each piece of logic lives in exactly one of them:

    config  - reads every setting from environment variables (nothing else reads os.environ)
    text    - defines what a "word" is (nothing else tokenizes text)
    corpus  - loads the texts once at startup and counts in them
    cache   - everything that touches Redis (key names, commands, failure policy)
    server  - the RPyC service: validates requests and combines corpus + cache
    client  - the client-side API, used by the CLI and by the load generator (bench/)
"""

__version__ = "0.2.0"  # 0.2.x = Phase 2 (single server)
