#!/usr/bin/env python3
"""The README's and the project page's examples are the binary's output, generated and checked.

Blocks, each between `<!-- gen:NAME -->` and `<!-- /gen:NAME -->` in README.md and docs/index.html:

  flow     a complete session run against the built program: a refusal with its repair, the broker, a retained
           message, a `$SYS` topic, the end record, and the authority row;
  counts   how many tests there are, counted from the files;
  numbers  the saturated fan-out medians of the benchmark, from bench/results.json;
  case_presence, case_refusal, case_sizing
           the three business cases of the page, each run against the built program.

    python3 scripts/site.py           # rewrite the blocks
    python3 scripts/site.py --check   # change nothing; exit 1 if a block is stale (CI)

Needs build/mqtt, mosquitto_pub and mosquitto_sub, and jq.
"""

import html
import json
import os
import pathlib
import re
import shutil
import signal
import statistics
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILES = [ROOT / "README.md", ROOT / "docs" / "index.html"]
SHOWN_PORT = 1883


def free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def run(argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, timeout=30, **kw)


def flow():
    env = dict(os.environ, PATH=str(ROOT / "build") + os.pathsep + os.environ["PATH"])
    port = free_port()
    p = str(port)
    lines = []

    def say(text):
        lines.append(text)

    def shown(text):
        return text.replace(p, str(SHOWN_PORT))

    # 1. a refusal, with the command that fixes it
    say("# A bound below its smallest value is refused, with the command that fixes it")
    say("$ mqtt serve --port %d --max-packet 10" % SHOWN_PORT)
    out = run(["mqtt", "serve", "--port", str(SHOWN_PORT), "--max-packet", "10"], env=env)
    say(out.stdout.splitlines()[0])
    say("# exit status %d" % out.returncode)

    # 2. the broker, a retained message, a $SYS topic, the end record
    say("")
    say("# Run it; a client keeps a retained reading; another reads it back; a statistic")
    say("$ mqtt serve --port %d > broker.log &" % SHOWN_PORT)
    broker = subprocess.Popen(["mqtt", "serve", "--port", p, "--stats-seconds", "0"], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.time() + 10
        first = ""
        while time.time() < deadline:
            first = broker.stdout.readline()
            if first:
                break
        say("$ head -1 broker.log")
        say(shown(first.rstrip()))
        say("$ mosquitto_pub -p %d -t sensors/door -m open -r -q 1" % SHOWN_PORT)
        run(["mosquitto_pub", "-p", p, "-t", "sensors/door", "-m", "open", "-r", "-q", "1"])
        say("$ mosquitto_sub -p %d -t 'sensors/#' -v -q 1 -C 1" % SHOWN_PORT)
        say(run(["mosquitto_sub", "-p", p, "-t", "sensors/#", "-v", "-q", "1", "-C", "1", "-W", "5"]).stdout.rstrip())
        say("$ mosquitto_sub -p %d -t '$SYS/broker/clients/connected' -v -C 1" % SHOWN_PORT)
        say(run(["mosquitto_sub", "-p", p, "-t", "$SYS/broker/clients/connected", "-v", "-C", "1", "-W", "5"]).stdout.rstrip())
        broker.send_signal(signal.SIGTERM)
        rest = broker.stdout.read()
        broker.wait(timeout=10)
    finally:
        if broker.poll() is None:
            broker.kill()
    say("$ kill -TERM %1; jq -c 'select(.type==\"end\") | {ok, complete, publishes, delivered}' broker.log")
    end = [json.loads(l) for l in (first + rest).splitlines() if l.strip()]
    end = [r for r in end if r.get("type") == "end"][0]
    say(json.dumps({k: end[k] for k in ("ok", "complete", "publishes", "delivered")}, separators=(",", ":")))

    # 3. what the compiler proved the program can do
    say("")
    say("# What the compiler proved this program can reach")
    say("$ mqtt introspect | jq -c '.authority | {bounded, effects}'")
    doc = json.loads(run(["mqtt", "introspect"], env=env).stdout)["authority"]
    say(json.dumps({"bounded": doc["bounded"], "effects": doc["effects"]}, separators=(",", ":")))
    return "\n".join(lines)


class Broker:
    """A broker on a free port for one case; `shown` rewrites that port to 1883 in what is printed."""

    def __init__(self, *args):
        self.env = dict(os.environ, PATH=str(ROOT / "build") + os.pathsep + os.environ["PATH"])
        self.port = free_port()
        self.p = str(self.port)
        self.proc = subprocess.Popen(["mqtt", "serve", "--port", self.p, "--stats-seconds", "0", *args], env=self.env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.first = self.proc.stdout.readline()

    def shown(self, text):
        return text.replace(self.p, str(SHOWN_PORT))

    def stop(self):
        self.proc.send_signal(signal.SIGTERM)
        rest = self.proc.stdout.read()
        self.proc.wait(timeout=10)
        return [json.loads(l) for l in (self.first + rest).splitlines() if l.strip()]

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()


def case_presence():
    b = Broker()
    out = []
    try:
        out.append("# A gateway on the plant network keeps its last reading and its state where anyone can read them")
        out.append("$ mosquitto_pub -p 1883 -t plant/line1/temp -m 71.5 -r -q 1")
        run(["mosquitto_pub", "-p", b.p, "-t", "plant/line1/temp", "-m", "71.5", "-r", "-q", "1"])
        out.append("$ mosquitto_pub -p 1883 -t plant/line1/status -m online -r -q 1")
        run(["mosquitto_pub", "-p", b.p, "-t", "plant/line1/status", "-m", "online", "-r", "-q", "1"])
        out.append("")
        out.append("# It registered a will; the power is cut (SIGKILL, no DISCONNECT) and the broker publishes the will")
        out.append("$ mosquitto_sub -p 1883 -i gw-line1 -t gw/in --will-topic plant/line1/status --will-payload offline --will-retain -q 1 &")
        out.append("$ kill -9 $!")
        gw = subprocess.Popen(["mosquitto_sub", "-p", b.p, "-i", "gw-line1", "-t", "gw/in", "--will-topic", "plant/line1/status",
                               "--will-payload", "offline", "--will-retain", "-q", "1"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.7)
        gw.kill()
        gw.wait()
        time.sleep(0.7)
        out.append("")
        out.append("# A dashboard that connects afterwards sees the last state of everything")
        out.append("$ mosquitto_sub -p 1883 -t 'plant/#' -v -C 2 | sort")
        got = run(["mosquitto_sub", "-p", b.p, "-t", "plant/#", "-v", "-C", "2", "-W", "5"]).stdout.split("\n")
        out.extend(sorted(l for l in got if l))
        b.stop()
    finally:
        b.kill()
    return "\n".join(out)


def case_refusal():
    b = Broker("--max-packet", "256")
    out = []
    try:
        out.append("# A test harness sets tight limits and asserts on what the broker refuses, by rule")
        out.append("$ mqtt serve --port 1883 --max-packet 256 > broker.log &")
        out.append("$ mosquitto_pub -p 1883 -t sensors/big -m \"$(head -c 1000 /dev/zero | tr '\\0' x)\"")
        run(["mosquitto_pub", "-p", b.p, "-t", "sensors/big", "-m", "x" * 1000])
        time.sleep(0.3)
        records = b.stop()
        out.append("$ jq -c 'select(.type==\"refusal\") | {rule, client_id}' broker.log")
        for r in records:
            if r.get("type") == "refusal":
                out.append(json.dumps({"rule": r["rule"], "client_id": r["client_id"]}, separators=(",", ":")))
        out.append("")
        out.append("# and what the broker does for that rule is data too")
        out.append("$ mqtt rules | jq -c 'select(.tag==\"limit.packet-size\")'")
        env = dict(os.environ, PATH=str(ROOT / "build") + os.pathsep + os.environ["PATH"])
        for l in run(["mqtt", "rules"], env=env).stdout.splitlines():
            if '"limit.packet-size"' in l:
                out.append(l)
    finally:
        b.kill()
    return "\n".join(out)


def case_sizing():
    out = ["# Know the memory before it accepts a connection: it is computed from the flags and printed at start"]
    for args in (["--max-connections", "1024"], ["--max-connections", "8192", "--queue-bytes", "65536"]):
        b = Broker(*args)
        try:
            out.append("$ mqtt serve --port 1883 %s | head -1" % " ".join(args))
            out.append(b.shown(b.first.rstrip()))
        finally:
            b.stop()
    return "\n".join(out)


def counts():
    py = sum(len(re.findall(r"^    def test_", p.read_text(), re.M)) for p in (ROOT / "tests" / "conformance").glob("test_*.py"))
    cho = sum(len(re.findall(r"^pub fn test_", p.read_text(), re.M)) for p in (ROOT / "tests").glob("*_test.cho"))
    return "%d black-box tests that read only what a client sees, and %d unit tests of the codec, the topic trie and the flag table" % (py, cho)


def numbers():
    results = json.loads((ROOT / "bench" / "results.json").read_text())
    names = [("mqtt", "cancho-mqtt"), ("mosquitto", "Mosquitto 2.0.18"), ("nanomq", "NanoMQ"), ("emqx", "EMQX 5.8.6"), ("vernemq", "VerneMQ 2.2.1")]
    rows = ["| one core | QoS 0 | QoS 1 | QoS 2 |", "|---|---:|---:|---:|"]
    for key, name in names:
        cell = results["%s/1core" % key]

        def med(c):
            v = [x["delivered_per_s"] for x in cell.get(c, [])]
            return "%dk" % round(statistics.median(v) / 1000) if v else "-"
        rows.append("| %s | %s | %s | %s |" % (name, med("fanout_qos0"), med("fanout_qos1"), med("fanout_qos2")))
    return "Deliveries a second at the subscribers, 4 publishers and 100 subscribers going as fast as they can, median of three or five runs:\n\n" + "\n".join(rows)


def latency():
    results = json.loads((ROOT / "bench" / "results.json").read_text())

    def p50(key):
        return round(statistics.median(x["p50_us"] for x in results["%s/1core" % key]["latency_idle0"]))

    def mem(key):
        return round(results["%s/1core" % key]["memory"]["mib_at_5000_connections"])
    return ("Latency with one message in flight: p50 %d microseconds (Mosquitto %d; the probe is Python, so a few microseconds either way is "
            "not evidence). Memory with 5,000 idle connections: %d MiB (Mosquitto %d)." % (p50("mqtt"), p50("mosquitto"), mem("mqtt"), mem("mosquitto")))


def ratio():
    results = json.loads((ROOT / "bench" / "results.json").read_text())
    r = statistics.median(x["delivery_ratio"] for x in results["mqtt/1core"]["fanout_qos2"])
    return "%d%%" % round(100 * r)


BLOCKS = {"flow": flow, "case_presence": case_presence, "case_refusal": case_refusal, "case_sizing": case_sizing, "counts": counts, "numbers": numbers, "latency": latency, "ratio": ratio}


def render(name, body, path):
    if path.suffix == ".html":
        if name == "flow" or name.startswith("case_"):
            return "<pre><code>" + html.escape(body) + "</code></pre>"
        if name == "numbers":
            lines = body.split("\n")
            rows = [[c.strip() for c in l.strip("|").split("|")] for l in lines if l.startswith("|") and not l.startswith("|---")]
            head, rest = rows[0], rows[1:]
            table = "<table><thead><tr>" + "".join("<th>%s</th>" % html.escape(h) for h in head) + "</tr></thead><tbody>"
            for r in rest:
                table += "<tr>" + "".join("<td>%s</td>" % html.escape(c) for c in r) + "</tr>"
            return "<p>" + html.escape(lines[0]) + "</p>" + table + "</tbody></table>"
        return html.escape(body)
    if name == "flow" or name.startswith("case_"):
        return "```console\n" + body + "\n```"
    return body


def main():
    check = "--check" in sys.argv[1:]
    if not (ROOT / "build" / "mqtt").exists():
        sys.exit("site: build/mqtt does not exist; run `cancho build`")
    for tool in ("mosquitto_pub", "mosquitto_sub", "jq"):
        if shutil.which(tool) is None:
            sys.exit("site: %s is not installed" % tool)
    generated = {name: fn() for name, fn in BLOCKS.items()}
    stale = []
    for path in FILES:
        if not path.exists():
            continue
        text = path.read_text()
        new = text
        for name, body in generated.items():
            pattern = re.compile(r"(<!-- gen:%s -->\n?)(.*?)(\n?<!-- /gen:%s -->)" % (name, name), re.S)
            if pattern.search(new):
                new = pattern.sub(lambda m: m.group(1) + render(name, body, path) + m.group(3), new)
        if new != text:
            if check:
                stale.append(path.name)
            else:
                path.write_text(new)
    if stale:
        print("site: stale generated blocks in %s (run scripts/site.py)" % ", ".join(stale))
        sys.exit(1)


if __name__ == "__main__":
    main()
