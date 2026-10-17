# Debrief notes (not part of the submission)

## Phase 2
- Client opens a new RPyC connection per request; latency = connect until reply arrives.
- Server: texts loaded at startup into word lists; `count` checks the text and keyword,
  increments `hot` (ZINCRBY), looks up `count:<text>:<word>`, on a miss counts with
  `list.count` and stores the result.
- Load generator: one thread per request at fixed intervals (rate), so slow replies do not
  slow the sending down. Redis flushed before every run -> every run starts cold.
- p99 grows before the mean: Python runs one thread at a time, requests that arrive close
  together wait for each other; misses are slow, hits fast -> uneven waiting.

## Phase 3
- LB = asyncio TCP proxy. Per client connection: choose server, open connection, copy bytes
  both ways (`pipe`). Never looks at RPyC messages -> that is why one connection per request.
- Least connections: fewest open connections; tie -> server used longest ago (round robin
  when idle).
- Least response time: lowest (open + 1) * average response time. Response time = duration
  of the forwarded connection, moving average 0.9/0.1. Not used for > 1 s -> counts as 0,
  otherwise a server that was slow once is never chosen and never measured again.
- Three servers slower than one at low load: extra hop, LB copies every byte.

## Phase 4
- Without health checks (`HEALTH_CHECKS=off`): stopped server still chosen. LC: its connections
  fail at once, so it never has open connections -> about a third of the requests fail. LRT: it
  keeps its old response time (failures are not measured) -> chosen when that is lowest and at
  least once per second -> fewer errors, but they never stop.
- `docker stop` sends SIGTERM; python as process 1 ignores it, so compose has `init: true`
  (a small init process that passes the signal on) -> the container stops at once.
- In Docker a stopped container's name no longer resolves -> connecting fails immediately.
- With health checks: status UP/DOWN per server, only UP servers go to the algorithm.
  - every 2 s a TCP connect to each server (timeout 1 s): fail -> DOWN, success -> UP
  - connect failure for a client -> DOWN immediately, same client tried on another server
    (safe: nothing was forwarded yet)
  - requests already running on the stopped server fail
  - only the health check can bring a server back (DOWN servers get no traffic)
  - no server UP -> try all anyway
- Limitation: a TCP check does not see a server that accepts connections but hangs
  (`docker pause`).

## Phase 4 run
`--progress` prints the time every 5 s; stop server2 at `20 s`, start it at `40 s`, and pass
those times as `--stop 20 --start 40` to `bench.plot timeline`.
