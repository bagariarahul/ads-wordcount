"""Experiment harness: generates workloads, drives the service, turns results into figures.

Kept out of the `wordcount` package on purpose: the server image does not ship
benchmark or plotting code (see the two Dockerfile targets).

    trace         - build the fixed request trace every experiment replays
    loadgen       - replay a trace open-loop at given rates, record per-request latency
    stats         - the latency statistics (mean, percentiles) used by loadgen AND plot
    plot          - run summaries -> report figures and LaTeX table
    rpc_messages  - diagnostic: which RPyC messages one request generates
"""
