#!/usr/bin/env python3
"""Tables for docs/benchmark.md from bench/results.json: medians, with the range of runs.

    python3 bench/report.py > /tmp/tables.md
"""

import json
import pathlib
import statistics as st

HERE = pathlib.Path(__file__).resolve().parent
R = json.loads((HERE / "results.json").read_text())
ORDER = [("mqtt", "lexsys-mqtt"), ("mosquitto", "Mosquitto 2.0.18"), ("nanomq", "NanoMQ"), ("emqx", "EMQX 5.8.6"),
         ("vernemq", "VerneMQ 2.2.1"), ("hivemq-ce", "HiveMQ CE")]


def runs(b, cores, cell):
    v = R.get("%s/%dcore" % (b, cores), {})
    return v.get(cell) or []


def spread(b, cores, cell, key, scale=1.0, fmt="%.0f"):
    v = [x[key] / scale for x in runs(b, cores, cell)]
    if not v:
        return "-"
    return (fmt + " (" + fmt + "-" + fmt + ")") % (st.median(v), min(v), max(v))


def med(b, cores, cell, key, fmt="%.0f"):
    v = [x[key] for x in runs(b, cores, cell)]
    return fmt % st.median(v) if v else "-"


def mem(b, cores, key):
    m = R.get("%s/%dcore" % (b, cores), {}).get("memory")
    return "%.0f" % m[key] if m else "-"


for cores in (1, 2):
    print("### %d core%s\n" % (cores, "" if cores == 1 else "s"))
    print("| Broker | Paced QoS 0 (20k/s offered), delivered/s | Saturated QoS 0, delivered/s | Saturated QoS 1, delivered/s | Saturated QoS 2, delivered/s |")
    print("|---|---:|---:|---:|---:|")
    for b, name in ORDER:
        print("| %s | %s | %s | %s | %s |" % (name, spread(b, cores, "fanout_qos0_paced", "delivered_per_s"),
                                       spread(b, cores, "fanout_qos0", "delivered_per_s", 1000, "%.0fk"),
                                       spread(b, cores, "fanout_qos1", "delivered_per_s", 1000, "%.1fk"),
                                       spread(b, cores, "fanout_qos2", "delivered_per_s", 1000, "%.1fk")))
    print()
    print("| Broker | Latency p50 / p99 / p99.9 µs, 0 idle connections | same, 1,000 idle | Memory MiB: idle / 1,000 / 5,000 connections | 5,000 connected |")
    print("|---|---|---|---|---:|")
    for b, name in ORDER:
        v = R.get("%s/%dcore" % (b, cores), {})
        l0 = "/".join(med(b, cores, "latency_idle0", k) for k in ("p50_us", "p99_us", "p999_us"))
        l1 = "/".join(med(b, cores, "latency_idle1000", k) for k in ("p50_us", "p99_us", "p999_us"))
        print("| %s | %s | %s | %s / %s / %s | %s |" % (name, l0, l1, mem(b, cores, "idle_mib"),
              mem(b, cores, "mib_at_1000_connections"), mem(b, cores, "mib_at_5000_connections"),
              v.get("connect", {}).get("connected", "-")))
    print()
