#!/usr/bin/env python3
"""Benchmark MQTT brokers on one machine, one at a time (docs/benchmark.md).

    python3 bench/run.py --brokers mosquitto,emqx --cores 1 --runs 3 --out results.json

Each broker runs in Docker with --network host and --cpuset-cpus pinned to its
cores; the load generator (emqtt-bench) is pinned to the last two cores of the
machine, and the latency probe to the last one. Cells follow docs/design.md
section 11. Everything measured is written to the JSON file, with the versions.
"""

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probe  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LOADGEN = "emqx/emqtt-bench:latest"
HOST = "127.0.0.1"
SETTLE_S = 8  # let a broker drain what the previous cell left queued
NCPU = os.cpu_count()
LOAD_CPUS = "%d,%d" % (NCPU - 2, NCPU - 1)

BROKERS = {
    "mosquitto": dict(image="eclipse-mosquitto:2", args=[], env={},
                      volumes=[HERE + "/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro"]),
    "emqx": dict(image="emqx/emqx:5.8.6", args=[], env={}, volumes=[]),
    "nanomq": dict(image="emqx/nanomq:latest", args=[], env={}, volumes=[]),
    "vernemq": dict(image="vernemq/vernemq:latest", args=[], volumes=[],
                    env={"DOCKER_VERNEMQ_ACCEPT_EULA": "yes", "DOCKER_VERNEMQ_ALLOW_ANONYMOUS": "on",
                         "DOCKER_VERNEMQ_LISTENER__TCP__DEFAULT": "0.0.0.0:1883"}),
    "hivemq-ce": dict(image="hivemq/hivemq-ce:latest", args=[], env={}, volumes=[]),
}


def sh(*cmd, check=True):
    return subprocess.run(cmd, capture_output=True, text=True, check=check).stdout


def start_broker(name, cores):
    b = BROKERS[name]
    cpus = ",".join(str(i) for i in range(cores))
    cmd = ["docker", "run", "-d", "--name", "broker", "--network", "host", "--cpuset-cpus", cpus,
           "--ulimit", "nofile=20000:20000"]
    for k, v in b["env"].items():
        cmd += ["-e", "%s=%s" % (k, v)]
    for v in b["volumes"]:
        cmd += ["-v", v]
    cmd += [b["image"]] + b["args"]
    sh(*cmd)
    t0 = time.time()
    if not probe.ready(HOST, 1883):
        logs = sh("docker", "logs", "--tail", "20", "broker", check=False)
        stop_broker()
        raise RuntimeError("%s did not become ready: %s" % (name, logs))
    return time.time() - t0


def stop_broker():
    sh("docker", "rm", "-f", "broker", check=False)


def broker_memory_mib():
    out = sh("docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", "broker").split("/")[0].strip()
    m = re.match(r"([\d.]+)([KMG]i?B)", out)
    if not m:
        raise RuntimeError("no memory reading (is the broker still running?): %r" % out)
    v = float(m.group(1))
    return v * {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024, "kB": 1 / 1024, "MB": 1, "GB": 1024}[m.group(2)]


def loadgen(name, *args):
    sh("docker", "run", "-d", "--name", name, "--network", "host", "--cpuset-cpus", LOAD_CPUS,
       "--ulimit", "nofile=20000:20000", LOADGEN, *args)


def loadgen_collect(*names):
    logs = {}
    for n in names:
        logs[n] = sh("docker", "logs", n, check=False) + sh("docker", "logs", n, check=False)
        sh("docker", "rm", "-f", n, check=False)
    return logs


def series(log, kind):
    """The per-second rates of lines like '5s recv total=N rate=R/sec'."""
    return [float(m.group(1)) for m in re.finditer(r"^\d+s %s total=\d+ rate=([\d.]+)/sec" % kind, log, re.M)]


def total(log, kind):
    t = [int(m.group(1)) for m in re.finditer(r"^\d+s %s total=(\d+)" % kind, log, re.M)]
    return t[-1] if t else 0


def steady(values, drop_head=3, drop_tail=2):
    v = values[drop_head:len(values) - drop_tail] if len(values) > drop_head + drop_tail else values
    v = [x for x in v if x > 0]
    return statistics.median(v) if v else 0.0


def fanout(qos, subs=100, pubs=4, size=64, seconds=16, interval_ms=0):
    loadgen("lg-sub", "sub", "-h", HOST, "-V", "4", "-c", str(subs), "-t", "bench/fan", "-q", str(qos), "-R", "1000")
    time.sleep(subs / 1000 + 3)
    args = ["pub", "-h", HOST, "-V", "4", "-c", str(pubs), "-t", "bench/fan", "-s", str(size), "-I", str(interval_ms), "-q", str(qos)]
    if qos:
        args += ["-F", "32"]
    loadgen("lg-pub", *args)
    time.sleep(seconds)
    logs = loadgen_collect("lg-sub", "lg-pub")
    recv = steady(series(logs["lg-sub"], "recv"))
    sent = steady(series(logs["lg-pub"], "pub"))
    return {"delivered_per_s": recv, "published_per_s": sent,
            "delivery_ratio": (recv / ((pubs * 1000.0 / interval_ms) * subs)) if interval_ms else ((recv / (sent * subs)) if sent else 0.0), "subscribers": subs, "publishers": pubs,
            "offered_msgs_per_s": (pubs * 1000.0 / interval_ms) if interval_ms else None}


def idle_connections(n, first=0, rate=1000):
    name = "lg-idle-%d" % first
    loadgen(name, "sub", "-h", HOST, "-V", "4", "-c", str(n), "-n", str(first), "-t", "bench/idle/%i", "-q", "0",
            "-R", str(rate), "-k", "300")
    time.sleep(n / 800 + 4)
    return name


def latency_cell(idle):
    names = []
    if idle:
        names.append(idle_connections(idle))
    os.sched_setaffinity(0, {NCPU - 1})
    s = probe.latency(HOST, 1883, samples=20000, warmup=3000)
    os.sched_setaffinity(0, set(range(NCPU)))
    loadgen_collect(*names)
    pct = lambda p: s[min(len(s) - 1, int(len(s) * p))] / 1000.0
    return {"idle_connections": idle, "samples": len(s), "p50_us": pct(0.5), "p90_us": pct(0.9),
            "p99_us": pct(0.99), "p999_us": pct(0.999), "max_us": s[-1] / 1000.0}


def memory_cell():
    out = {"idle_mib": broker_memory_mib()}
    names = [idle_connections(1000, 0)]
    out["mib_at_1000_connections"] = broker_memory_mib()
    names.append(idle_connections(4000, 1000))
    out["mib_at_5000_connections"] = broker_memory_mib()
    logs = loadgen_collect(*names)
    out["connected"] = sum(total(l, "connect_succ") for l in logs.values())
    if out["connected"] < 4950:
        out["warning"] = "fewer than 5000 connections were established; the 5000 figure is not comparable"
    return out


def connect_cell(n=5000):
    loadgen("lg-conn", "conn", "-h", HOST, "-V", "4", "-c", str(n), "-R", "5000", "-k", "60")
    time.sleep(12)
    log = loadgen_collect("lg-conn")["lg-conn"]
    rates = series(log, "connect_succ")
    return {"connected": total(log, "connect_succ"), "target": n, "peak_per_s": max(rates) if rates else 0.0,
            "note": "bounded by the load generator, not necessarily the broker"}


def image_id(image):
    return sh("docker", "image", "inspect", "--format", "{{.Id}}", image, check=False).strip()


CELLS = [
    # A load every broker should sustain: 4 publishers x 50/s x 100 subscribers = 20,000 deliveries/s.
    ("fanout_qos0_paced", lambda: fanout(0, interval_ms=20, seconds=14)),
    ("fanout_qos0", lambda: fanout(0)),
    ("fanout_qos1", lambda: fanout(1)),
    ("latency_idle0", lambda: latency_cell(0)),
    ("latency_idle1000", lambda: latency_cell(1000)),
]


def run_broker(name, cores, runs, only=None, restart=False, previous=None):
    """Every cell of design section 11 for one broker. `restart` gives every run a freshly started broker
    (a broker that collapsed under the previous cell must not poison the next); `only` re-runs some cells and
    keeps the rest of `previous`."""
    result = dict(previous or {})
    result.update({"image": BROKERS[name]["image"], "image_id": image_id(BROKERS[name]["image"]), "cores": cores,
                   "restart_per_run": restart})
    want = lambda cell: only is None or cell in only
    result["start_s"] = start_broker(name, cores)
    try:
        for cell, fn in CELLS:
            if not want(cell):
                continue
            result[cell] = []
            for r in range(runs):
                print("  %s run %d" % (cell, r + 1), flush=True)
                if restart:
                    stop_broker()
                    start_broker(name, cores)
                time.sleep(SETTLE_S)
                try:
                    result[cell].append(fn())
                except Exception as e:  # one cell failing does not hide the others
                    result.setdefault("cell_errors", []).append({"cell": cell, "run": r + 1, "error": repr(e)})
                    print("  cell failed: %r" % e, flush=True)
                    loadgen_collect(*[n for n in sh("docker", "ps", "-a", "--format", "{{.Names}}").split() if n.startswith("lg-")])
        if want("memory"):
            print("  memory", flush=True)
            if restart:
                stop_broker()
                start_broker(name, cores)
            time.sleep(SETTLE_S)
            try:
                result["memory"] = memory_cell()
            except Exception as e:
                result.setdefault("cell_errors", []).append({"cell": "memory", "run": 1, "error": repr(e)})
                loadgen_collect(*[n for n in sh("docker", "ps", "-a", "--format", "{{.Names}}").split() if n.startswith("lg-")])
    finally:
        stop_broker()
    if want("connect"):
        start_broker(name, cores)
        try:
            print("  connect", flush=True)
            result["connect"] = connect_cell()
        finally:
            stop_broker()
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brokers", default=",".join(BROKERS))
    ap.add_argument("--cores", type=int, default=1)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="", help="comma-separated cells to re-run, keeping the others")
    ap.add_argument("--restart-per-run", action="store_true")
    a = ap.parse_args()
    results = {}
    if os.path.exists(a.out):
        results = json.load(open(a.out))
    for name in a.brokers.split(","):
        key = "%s/%dcore" % (name, a.cores)
        print(key, flush=True)
        try:
            only = set(a.only.split(",")) if a.only else None
            results[key] = run_broker(name, a.cores, a.runs, only, a.restart_per_run, results.get(key) if only else None)
        except Exception as e:  # recorded, not hidden
            results[key] = {"error": str(e)}
            print("  FAILED: %s" % e, flush=True)
        json.dump(results, open(a.out, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
