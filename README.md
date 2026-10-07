<p align="center"><img src="docs/logo.png" alt="cancho-mqtt" width="260"></p>

# cancho-mqtt

[![ci](https://github.com/alpibrusl/cancho-mqtt/actions/workflows/ci.yml/badge.svg)](https://github.com/alpibrusl/cancho-mqtt/actions/workflows/ci.yml)

**An MQTT broker you can reason about.** One thread and one poller, memory sized at start, every limit a flag with a ceiling, every refusal a named rule with an exact count, and an authority report the compiler proves: this program cannot touch a file, dial out or call foreign code. MQTT 3.1.1 over plain TCP (and 3.1 clients), QoS 0, 1 and 2, retained messages, wills, persistent sessions and `$SYS`. Written in [cancho](https://github.com/alpibrusl/cancho). For a person it is a small broker that tells you exactly why it refused something; for an agent it is one with `introspect`, `rules` and `skill` commands generated from the tables it runs on.

**Status: alpha, for a trusted network.** Anonymous, in memory, plain TCP: no authentication, no TLS, nothing survives a restart, and no long soak has been run. Put it behind something that does those, or on a network where that is acceptable. What it cannot do yet is listed [below](#what-it-cannot-do-yet).

## One complete flow

A refusal with the command that fixes it; the broker; a retained reading; a `$SYS` statistic; and what the compiler proved the program can reach. This is the built program's own output; `scripts/site.py --check` fails CI if it drifts.

<!-- gen:flow -->
```console
# A bound below its smallest value is refused, with the command that fixes it
$ mqtt serve --port 1883 --max-packet 10
{"type":"error","error":{"code":"INVALID_ARGS","rule":"args.out-of-range","message":"a flag's value is outside the range this broker accepts","hint":"use a value within the range introspect names for the flag","repair":{"kind":"retry","argv":["mqtt","serve","--port","1883","--max-packet","64"]},"detail":{"flag":"max-packet","value":10,"minimum":64,"ceiling":262144}}}
# exit status 2

# Run it; a client keeps a retained reading; another reads it back; a statistic
$ mqtt serve --port 1883 > broker.log &
$ head -1 broker.log
{"type":"listening","port":1883,"max_connections":1024,"max_packet":16384,"memory_bytes":73099264,"t_ms":0}
$ mosquitto_pub -p 1883 -t sensors/door -m open -r -q 1
$ mosquitto_sub -p 1883 -t 'sensors/#' -v -q 1 -C 1
sensors/door open
$ mosquitto_sub -p 1883 -t '$SYS/broker/clients/connected' -v -C 1
$SYS/broker/clients/connected 1
$ kill -TERM %1; jq -c 'select(.type=="end") | {ok, complete, publishes, delivered}' broker.log
{"ok":true,"complete":true,"publishes":1,"delivered":2}

# What the compiler proved this program can reach
$ mqtt introspect | jq -c '.authority | {bounded, effects}'
{"bounded":true,"effects":["args","clock","conn_accept","conn_read","conn_write","err_write","heap","io_write","net_in","poll","signals","signals_read"]}
```
<!-- /gen:flow -->

**[Download the binary](https://github.com/alpibrusl/cancho-mqtt/releases/download/latest/mqtt-linux-x86_64)** (Linux x86-64, built and tested by CI on the latest commit of `main`; [checksum and authority report](https://github.com/alpibrusl/cancho-mqtt/releases/tag/latest)) · [project page](https://alpibrusl.github.io/cancho-mqtt/)

## Why cancho-mqtt?

Most brokers optimise for features and scale out. This one optimises for being *predictable and checkable* on one core.

* **Bounded.** The tables are allocated at start from flags (`mqtt introspect` lists each with its default and ceiling), so memory has a stated ceiling and does not follow the workload: resident size stays flat under churn (a test asserts it), and an idle connection costs about 0.3 KiB. [docs/benchmark.md](docs/benchmark.md)
* **Checkable authority.** The compiler derives what the program can reach: no files, no outbound connections, no foreign code, one listener. `mqtt introspect` prints it, it is embedded in the binary, and CI fails if it differs from the committed [`manifests/mqtt.authority.json`](manifests/mqtt.authority.json) or exceeds [`ceiling.toml`](ceiling.toml). Four mutants of the gate itself must be refused. [docs/design.md](docs/design.md) section 2
* **Every refusal has a tag.** 28 connection-level rules (`protocol.*`, `limit.*`, `timeout.*`), each with a fixture, an action and an exact counter; the log reports the first of a repeat and how many it suppressed. `mqtt rules` lists them.
* **Errors and logs are data.** A process error is one JSON line with a rule, a hint and, where a script can apply it safely, a repair that never widens authority. The log is bounded NDJSON that ends with an `end` record, and no payload is ever logged. `--format text` is for people.
* **Tested against Mosquitto.** The same scenarios, scripted and generated, run against this broker and Mosquitto 2.0.18, and what each client sees is compared. The two differences are written down and asserted. [docs/design.md](docs/design.md) section 9
* **Measured.** Against five other brokers, with the conditions and what was *not* measured. [docs/benchmark.md](docs/benchmark.md)

## Where it would earn its place

Three scenarios, each run against the built program (`scripts/site.py --check` fails CI if the output drifts). They illustrate the properties above; they are not reports of deployments.

**1. Plant-floor gateways and dashboards: last value and presence.** Retained messages hold the last reading, a will turns a dead gateway into an `offline` message, and a dashboard that connects later needs no polling.

<!-- gen:case_presence -->
```console
# A gateway on the plant network keeps its last reading and its state where anyone can read them
$ mosquitto_pub -p 1883 -t plant/line1/temp -m 71.5 -r -q 1
$ mosquitto_pub -p 1883 -t plant/line1/status -m online -r -q 1

# It registered a will; the power is cut (SIGKILL, no DISCONNECT) and the broker publishes the will
$ mosquitto_sub -p 1883 -i gw-line1 -t gw/in --will-topic plant/line1/status --will-payload offline --will-retain -q 1 &
$ kill -9 $!

# A dashboard that connects afterwards sees the last state of everything
$ mosquitto_sub -p 1883 -t 'plant/#' -v -C 2 | sort
plant/line1/status offline
plant/line1/temp 71.5
```
<!-- /gen:case_presence -->

**2. Test and staging brokers that explain themselves.** Every refusal is a named rule with a log record a test can assert on.

<!-- gen:case_refusal -->
```console
# A test harness sets tight limits and asserts on what the broker refuses, by rule
$ mqtt serve --port 1883 --max-packet 256 > broker.log &
$ mosquitto_pub -p 1883 -t sensors/big -m "$(head -c 1000 /dev/zero | tr '\0' x)"
$ jq -c 'select(.type=="refusal") | {rule, client_id}' broker.log
{"rule":"limit.packet-size","client_id":"auto-1"}

# and what the broker does for that rule is data too
$ mqtt rules | jq -c 'select(.tag=="limit.packet-size")'
{"type":"rule","tag":"limit.packet-size","action":"close"}
```
<!-- /gen:case_refusal -->

**3. Small fixed boxes: size it before you deploy it.** The footprint is a number printed on the first log line, not a function of the workload.

<!-- gen:case_sizing -->
```console
# Know the memory before it accepts a connection: it is computed from the flags and printed at start
$ mqtt serve --port 1883 --max-connections 1024 | head -1
{"type":"listening","port":1883,"max_connections":1024,"max_packet":16384,"memory_bytes":73099264,"t_ms":0}
$ mqtt serve --port 1883 --max-connections 8192 --queue-bytes 65536 | head -1
{"type":"listening","port":1883,"max_connections":8192,"max_packet":16384,"memory_bytes":713771008,"t_ms":0}
```
<!-- /gen:case_sizing -->

## Quick start

You need `git`, Rust, `gcc`, Python 3 and, to try it with real clients, the `mosquitto-clients` package.

```sh
git clone https://github.com/alpibrusl/cancho                         # the compiler
git clone https://github.com/alpibrusl/cancho-mqtt && cd cancho-mqtt
REV=$(sed -n 's/^cancho *= *"\(.*\)".*/\1/p' cancho.toml)             # the compiler these sources need
(cd ../cancho && git fetch -q origin && git checkout "$REV" && cargo build --release -p cancho)
export PATH=$PWD/../cancho/target/release:$PATH
cancho build                                                          # builds build/mqtt
build/mqtt serve --port 1883
```

`mqtt serve` runs until `SIGINT` or `SIGTERM`, which end the log with its `end` record. Its standard output is the log, so run it under something that always reads it (a supervisor, `> broker.log`): [a reader that stops stalls the broker](#what-it-cannot-do-yet).

```sh
build/mqtt introspect      # flags, limits, rules, exit codes, authority, as one JSON document
build/mqtt rules           # every connection-level rule and what the broker does
build/mqtt skill           # the same, as an agentskills.io SKILL.md
build/mqtt serve --max-connections 8192 --queue-bytes 65536    # every bound is a flag
```

## What you get

* **MQTT 3.1.1, and 3.1 clients** (`MQIsdp`, level 3). CONNECT, keep-alive at 1.5 times the interval, clean and persistent sessions, takeover, wills (not published after a DISCONNECT, published after a takeover as Mosquitto does).
* **QoS 0, 1 and 2**, with an in-flight window, DUP redelivery on resume, and the PUBREL after a PUBREC resent rather than the PUBLISH. Inbound QoS 2 is routed when the PUBLISH arrives (a resend is acknowledged, not routed twice); Mosquitto routes at PUBREL, and the difference is asserted. [design section 7a](docs/design.md)
* **Retained messages** in a fixed table, and **bounded per-session queues** that drop the newest message for a slow subscriber, count it, and close a connection that makes no write progress for 30 s.
* **`$SYS`**: 17 topics under Mosquitto's names (clients, messages, bytes, subscriptions, uptime, version), with the retain flag on the first delivery and not after, as Mosquitto does it. Not `load/*`, `heap/*` or `store/*`. [design section 7b](docs/design.md)
* **Topic matching** with `+` and `#`, the `$` rule, and one copy per subscriber at the highest QoS granted (Mosquitto delivers it at the first match's QoS; asserted).
* **Hostile input:** 6,400 hostile connections, split and malformed packets, slowloris and tight bounds are in the tests; no input reaches a panic.

## Built to be read by an agent

`mqtt introspect`, `mqtt rules` and `mqtt skill` are generated from the same tables the code parses its flags and names its rules with, so they cannot disagree with the program. A process error carries `{code, rule, message, hint, repair, detail}` against the schema [`schemas/mqtt.v1.json`](schemas/mqtt.v1.json), exit codes follow the toolbox's table, and the `repair` is a command only where applying it cannot widen what the program may do. The CLI, errors and log follow cancho's agent toolbox through the `contract/` package of [cancho-tools](https://github.com/alpibrusl/cancho-tools), pinned by commit.

## What it cannot do yet

* **No authentication, no TLS.** Anonymous clients on plain TCP. cancho now has a TLS 1.3 server (signer, engine and an example; not independently reviewed); this broker has no transport layer for it yet, and client certificates are not built there. [docs/later.md](docs/later.md)
* **Nothing survives a restart:** sessions, queues and retained messages are in memory only.
* **MQTT 5, WebSockets, clustering, shared subscriptions:** not built. A terminating proxy covers TLS and WebSockets today.
* **`$SYS`** has no `load/*`, `heap/*` or `store/*` topics.
* **A log reader that stops reading stalls the broker.** The log goes to standard output with a blocking write and cancho has no non-blocking one. Measured: about 86 bytes a second idle (13 minutes to fill a 64 KiB pipe) and 1.8 KB a second under a flood of refusals (36 s). A test pins it. [design section 5a](docs/design.md)
* **One core.** One thread; a second core is a second process. In every cell run so far one core was enough.
* **No soak yet:** no run of days, and no sanitizer-style run.

[docs/later.md](docs/later.md) says what each missing piece would cost, in what order, and what it would do to the authority report.

## How fast

Supporting evidence, not the point. One core each, one shared 4-core VM, defaults for every broker; **read the caveats before quoting a number**. The QoS 0 figure for this broker is a lower bound (it used about two thirds of its core, so the load generator was the limit); its QoS 2 figure counts deliveries, and this broker drops the newest message under overload where others slow the publishers, so only about <!-- gen:ratio -->13%<!-- /gen:ratio --> of what was offered arrived.

<!-- gen:numbers -->
Deliveries a second at the subscribers, 4 publishers and 100 subscribers going as fast as they can, median of three or five runs:

| one core | QoS 0 | QoS 1 | QoS 2 |
|---|---:|---:|---:|
| cancho-mqtt | 1222k | 199k | 112k |
| Mosquitto 2.0.18 | 344k | 175k | 9k |
| NanoMQ | 107k | 72k | 20k |
| EMQX 5.8.6 | 57k | 36k | 20k |
| VerneMQ 2.2.1 | 307k | 11k | 8k |
<!-- /gen:numbers -->

<!-- gen:latency -->Latency with one message in flight: p50 42 microseconds (Mosquitto 46; the probe is Python, so a few microseconds either way is not evidence). Memory with 5,000 idle connections: 14 MiB (Mosquitto 38).<!-- /gen:latency --> What was not measured, the QoS 1 and 2 results that are not explained, and the conditions are in [docs/benchmark.md](docs/benchmark.md).

## Learn more

| | |
|---|---|
| [project page](https://alpibrusl.github.io/cancho-mqtt/) | the flow, the evidence and the numbers, in one page |
| [docs/design.md](docs/design.md) | the design, written before the code and corrected in place: scope, authority, memory, rules, the agent surface, QoS 2, `$SYS`, the gates, and the gaps |
| [docs/benchmark.md](docs/benchmark.md) | the comparison with Mosquitto, NanoMQ, EMQX, VerneMQ and HiveMQ CE, with conditions |
| [docs/conformance.md](docs/conformance.md) | which statements of the specification a test names, and which are not covered |
| [docs/later.md](docs/later.md) | what is not built, what it would cost, in what order |

## Contributing

Every change goes through what CI runs: `cancho fmt --check src tests generated`, `cancho build`, `cancho test`, the schema, manifest, coverage and site checks, the four authority mutants, the line limit, and the conformance, interop and differential suite against Mosquitto. Design before code, in `docs/design.md`, with claims measured; a claim that turns out false is corrected in place. No source file over 2,000 lines, every refusal has a rule tag, no input reaches a panic.

<!-- gen:counts -->
155 black-box tests that read only what a client sees, and 25 unit tests of the codec, the topic trie and the flag table
<!-- /gen:counts -->

## Licence

[EUPL-1.2](LICENSE).
