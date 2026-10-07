# cancho-mqtt

An MQTT 3.1.1 broker written in [cancho](https://github.com/alpibrusl/cancho): no `Ffi`, no `unsafe`, no file access,
and an authority report the compiler derives and CI checks. One thread on one `Poller`, memory with a ceiling stated at
start, every input bounded, and an agent-first command line, error and log surface
([docs/design.md](docs/design.md) section 5a).

```sh
cancho build                                 # needs the compiler named in cancho.toml
build/mqtt serve --port 1883                  # NDJSON log on stdout; SIGINT or SIGTERM ends it with an end record
build/mqtt introspect                         # flags, limits, rules, exit codes, authority, as JSON
build/mqtt skill                              # the same as an agentskills.io SKILL.md
build/mqtt rules                              # every connection-level rule and what the broker does
```

**Implemented:** CONNECT/CONNACK (MQTT 3.1.1, and 3.1 clients that send `MQIsdp`), keep-alive, clean and persistent sessions, will messages, SUBSCRIBE with `+` and `#`,
PUBLISH at QoS 0, 1 and 2 (in-flight window, DUP redelivery; QoS 2 delivers an inbound message when its PUBLISH arrives, not
when its PUBREL does, which 3.1.1 allows and Mosquitto does the other way: [design section 7a](docs/design.md)), retained
messages, `$SYS` statistics under Mosquitto's topic names (17 of them, not the `load`, `heap` or `store` ones), bounded per-session queues, every bound a flag with a stated ceiling. **Not implemented:** persistence across
restarts, TLS, authentication, MQTT 5, websockets, `$SYS`, clustering; [docs/later.md](docs/later.md) says why each is out
and what taking it up would cost.

## What has been checked, and how

| What | Where | Run by |
|---|---|---|
| Codec, topics, trie (incl. the trie against the reference matcher for every filter and name of up to three levels), config | `tests/*.cho`, `cancho test` | CI |
| Every connection-level rule has a fixture (28), the protocol statements the broker claims | `tests/conformance/test_rules.py`, `test_protocol.py`; [docs/conformance.md](docs/conformance.md) | CI |
| The agent-first surface: flag table equals design section 4, every log line validates against `schemas/mqtt.v1.json`, repairs applied by script, graceful stop | `test_cli.py` | CI |
| Real clients: `paho-mqtt`, `mosquitto_pub`/`mosquitto_sub` | `test_interop.py` | CI |
| What clients observe equals Mosquitto's, on written and generated scenarios; two differences written down and asserted | `test_differential.py` | CI |
| Hostile input: 6,400 random, mutated, oversized and split connections; slowloris; tight bounds | `test_fuzz.py` | CI |
| Resident memory flat under churn and under the stated ceiling | `test_memory.py` | CI |
| Authority within `ceiling.toml`, and the gate refuses four mutants | `scripts/manifest.py`, `scripts/mutants.py` | CI |
| Throughput, latency and memory against Mosquitto, EMQX, NanoMQ, VerneMQ, HiveMQ CE | `bench/`, `docs/benchmark.md` | by hand |

What has **not** been checked is listed where it is claimed: the end of [docs/conformance.md](docs/conformance.md), the
"Gap" list in [docs/design.md](docs/design.md) section 12, and the caveats of [docs/benchmark.md](docs/benchmark.md).

## Layout

`src/` the broker (`wire` codec, `topic` and `subs` the subscription trie, `tables` the tables and queues, `broker` the
connections and routing, `logs`, `config`, `rules`, `main`); `tests/` cancho unit tests and `tests/conformance/` the
black-box suite; `scripts/` the gates; `bench/` the benchmark harness; `docs/design.md` is written before the code and
corrected in place where building or measuring disagreed. [`CLAUDE.md`](CLAUDE.md) has the rules for changing it.
