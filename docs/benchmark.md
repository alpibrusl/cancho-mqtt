# Benchmark: where lexsys-mqtt stands

> **Status: measured once, on one shared 4-core VM, and caveated below. Not a verdict.** Cells were fixed in
> `docs/design.md` section 11 before the broker existed; what was actually run differs from that plan in ways listed
> under "Cells as run", and the design section is corrected to match. Raw results are `bench/results.json`; the harness is
> `bench/run.py`, the tables below are `python3 bench/report.py`.

## What was compared

lexsys-mqtt at `81e6959` (and the commits after it that touch only documents and tests), against the five brokers below,
each from its stock Docker image at the version the image carries on 2026-10-06, with its defaults except what it needs to
accept anonymous clients on port 1883. **Not measured:** rumqttd, FlashMQ, Mochi, and any other open-source broker; "the top
open-source brokers" here means these five.

| Broker | Image | Notes |
|---|---|---|
| Mosquitto 2.0.18 | `eclipse-mosquitto:2` | one listener, anonymous |
| NanoMQ | `emqx/nanomq:latest` | defaults |
| EMQX 5.8.6 | `emqx/emqx:5.8.6` | defaults; Erlang |
| VerneMQ 2.2.1 | `vernemq/vernemq:latest` | listener bound to `0.0.0.0:1883`, anonymous |
| HiveMQ CE | `hivemq/hivemq-ce:latest` | defaults; JVM with its default heap |
| lexsys-mqtt | `build/mqtt` mounted into `emqx/emqtt-bench:latest` | defaults except `--max-connections 8192`, so the 5,000-connection cells fit |

## Method

One machine (4 vCPUs of a shared cloud VM, 16 GiB, Linux 6.18), brokers one at a time in Docker with `--network host` and
`--cpuset-cpus` pinned to **1** and then **2** cores; the load generator ([`emqtt-bench`](https://github.com/emqx/emqtt-bench),
image `emqx/emqtt-bench:latest`) on the last two cores. Each cell is run 3 times (5 for lexsys-mqtt, where the result varies)
and the median is reported with the range of runs in brackets. Mosquitto and NanoMQ are single-threaded or nearly so, as is
lexsys-mqtt: a second core is for the kernel's loopback work and the other processes, not for the broker's own threads.

| Cell | What it does | Reported |
|---|---|---|
| Paced fan-out | 4 publishers x 50 messages/s, 64-byte payloads, QoS 0, 100 subscribers: 20,000 deliveries/s offered, a load every broker should sustain | deliveries per second at the subscribers |
| Saturated fan-out QoS 0 and QoS 1 | the same with the publishers going as fast as they can (`-I 0`; QoS 1 with 32 in flight, subscribers at QoS 1) | deliveries per second at the subscribers |
| Latency | a Python raw-socket probe: publish, wait for it at a second connection, one message in flight, 20,000 samples after 3,000 of warm-up; with 0 and with 1,000 idle connections held open | p50, p99, p99.9 in microseconds |
| Memory | the broker container's memory as `docker stats` reports it, idle, with 1,000 and with 5,000 idle subscriber connections | MiB |
| Connect | 5,000 clients, as fast as the generator goes | connections established (the rate is the generator's, see below) |

## Results

Medians, with the range of runs in brackets.

### 1 core

| Broker | Paced QoS 0 (20k/s offered), delivered/s | Saturated QoS 0, delivered/s | Saturated QoS 1, delivered/s |
|---|---:|---:|---:|
| lexsys-mqtt | 20000 (19978-20000) | 349k (346k-619k) | 77.6k (71.5k-81.8k) |
| Mosquitto 2.0.18 | 20000 (20000-20000) | 542k (513k-576k) | 61.9k (61.0k-70.6k) |
| NanoMQ | 19996 (19995-20000) | 135k (134k-141k) | 80.3k (79.9k-93.0k) |
| EMQX 5.8.6 | 20000 (19983-20000) | 48k (47k-49k) | 32.4k (31.0k-32.7k) |
| VerneMQ 2.2.1 | 20000 (20000-20008) | 325k (320k-327k) | 10.8k (10.6k-10.9k) |
| HiveMQ CE | 20000 (20000-20000) | 0k (0k-0k) | 8.2k (7.6k-9.2k) |

| Broker | Latency p50 / p99 / p99.9 µs, 0 idle connections | same, 1,000 idle | Memory MiB: idle / 1,000 / 5,000 connections | 5,000 connected |
|---|---|---|---|---:|
| lexsys-mqtt | 42/78/143 | 42/77/113 | 16 / 17 / 56 | 5000 |
| Mosquitto 2.0.18 | 46/84/226 | 47/87/218 | 3 / 10 / 39 | 5000 |
| NanoMQ | 66/112/357 | 70/117/182 | 1 / 14 / 69 | 5000 |
| EMQX 5.8.6 | 71/126/338 | 69/114/165 | 215 / 219 / 315 | 5000 |
| VerneMQ 2.2.1 | 71/124/208 | 93/341/778 | 227 / 243 / 363 | 5000 |
| HiveMQ CE | 161/4570/6310 | 179/4848/6249 | 229 / 246 / 284 | 5000 |

### 2 cores

| Broker | Paced QoS 0 (20k/s offered), delivered/s | Saturated QoS 0, delivered/s | Saturated QoS 1, delivered/s |
|---|---:|---:|---:|
| lexsys-mqtt | 20000 (20000-20000) | 639k (618k-667k) | 77.5k (63.2k-83.2k) |
| Mosquitto 2.0.18 | 20000 (19999-20000) | 545k (528k-554k) | 65.4k (63.8k-68.6k) |
| NanoMQ | 19998 (19972-20000) | 184k (175k-187k) | 111.5k (101.4k-114.2k) |
| EMQX 5.8.6 | 20000 (20000-20000) | 87k (86k-90k) | 55.9k (55.7k-57.5k) |
| VerneMQ 2.2.1 | 20000 (19990-20000) | 559k (556k-569k) | 17.0k (16.1k-17.9k) |
| HiveMQ CE | 20004 (20001-20013) | 0k (0k-0k) | 18.2k (17.0k-18.5k) |

| Broker | Latency p50 / p99 / p99.9 µs, 0 idle connections | same, 1,000 idle | Memory MiB: idle / 1,000 / 5,000 connections | 5,000 connected |
|---|---|---|---|---:|
| lexsys-mqtt | 42/74/127 | 43/82/230 | 16 / 17 / 56 | 5000 |
| Mosquitto 2.0.18 | 47/92/135 | 47/94/157 | 4 / 11 / 40 | 5000 |
| NanoMQ | 87/150/366 | 85/146/294 | 2 / 15 / 69 | 5000 |
| EMQX 5.8.6 | 73/180/367 | 75/177/362 | 221 / 222 / 349 | 5000 |
| VerneMQ 2.2.1 | 84/194/587 | 123/758/1415 | 335 / 351 / 453 | 5000 |
| HiveMQ CE | 161/561/4498 | 161/415/4098 | 188 / 247 / 306 | 5000 |


## Reading them

**Say these.**

- *Saturated QoS 1 fan-out:* lexsys-mqtt delivers about 78k/s on one core against Mosquitto's 62k/s and is comparable
  to NanoMQ's 80k/s; with two cores NanoMQ is ahead (111k/s against 78k/s). EMQX, VerneMQ and HiveMQ CE are well below.
- *Latency with one message in flight:* lexsys-mqtt's p50 is 42 microseconds against Mosquitto's 46 and the others'
  66 to 161, and its p99.9 is the lowest of the six on one core. **The probe is Python**, so a few microseconds either way is
  not evidence of anything; the gap to the Erlang and JVM brokers is.
- *Memory with 5,000 connections:* lexsys-mqtt takes 56 MiB, between Mosquitto (39) and NanoMQ (69), and a fifth of EMQX,
  VerneMQ or HiveMQ CE.
- *Paced fan-out:* every broker delivers all 20,000 messages a second; at that load nothing separates them.

**Do not say these.**

- *Saturated QoS 0 fan-out is not "faster than Mosquitto".* lexsys-mqtt's result is **bimodal from run to run**: on one core
  about 350k deliveries/s in 9 of 23 runs and 600k to 700k in the other 14, with a fresh broker per run as often as with a
  long-lived one. Five-run medians therefore land in either mode (349k and 639k above, for one and two cores), and the honest
  statement is "between roughly 350k and 700k, Mosquitto's 513k to 576k in between". The cause has not been found. A guess,
  not a finding: how many events one wait returns, and so how many writes one flush batches, may settle into a fast or a slow
  state. **Open question, issue #12.**
- *HiveMQ CE's saturated QoS 0 result of about 0 is a collapse, not a speed.* Four publishers going flat out on one or two
  cores put it into a state where it stops delivering and its memory climbs to about 4 GiB, and it did not recover until
  restarted; its paced result is fine. Every HiveMQ run here starts from a freshly started container for that reason.
- *No number here is a throughput limit.* "Saturated" means the publishers are faster than the broker can fan out, so brokers
  drop messages (all five do; lexsys-mqtt counts them as `limit.queue`), and the figure is what reached the subscribers.
- *The connect cell says nothing about the brokers.* Every one connected 5,000 clients at the generator's ceiling of about 2,400
  to 3,400 connections a second.

## Caveats

- **One shared VM**, with noise I cannot see, and no repeat on another machine. Differences under about 10% are not claims.
- **Defaults.** Nothing was tuned for any broker. A tuned Mosquitto (queue lengths, `max_inflight_messages`), EMQX with fewer
  schedulers, or HiveMQ CE with a sized heap would move the numbers, in ways this benchmark does not measure.
- **Memory is `docker stats`** (a cgroup figure that includes page cache and, for lexsys-mqtt, the mounted binary and the
  tables it writes at start), not resident size. For lexsys-mqtt at default bounds the process's own resident size is about 3 MiB
  idle (`tests/conformance/test_memory.py`); the 16 MiB idle figure above is with `--max-connections 8192`.
- **lexsys-mqtt runs from the load generator's image** because Docker Hub was rate-limiting a pull of a plain OS image; the
  binary needs only glibc 2.34.
- **The latency probe is not a load test**: one message in flight and nothing else running, so it measures a quiet path, not tail
  latency under load.
- **The machine was not always otherwise idle.** While the incumbents' runs were in progress I sometimes compiled and ran
  tests on the same VM, and one of lexsys-mqtt's early runs was disturbed that way and replaced. lexsys-mqtt's reported runs
  were made with the machine idle; the incumbents' were not always. Medians and ranges are shown, and small differences
  should be read with that in mind.
- **Effort was uneven.** lexsys-mqtt's saturated QoS 0 path was profiled by guessing and changed once (a log event per refused
  message became a counter; about 300k to about 650k in the fast mode, see below). No other broker was tuned.

## Cells as run, against the plan in the design

`docs/design.md` section 11 planned B1 to B6. B1 and B2 (fan-out, QoS 0 and 1) were run with 4 publishers and 100 subscribers
instead of 1 publisher, because one `emqtt-bench` publisher could not saturate any of them. B3 and B4 (many connections, latency
percentiles) became the closed-loop probe above with 0 and 1,000 idle connections; **a latency-at-fixed-offered-load cell was
not run**. B5 was run without retained messages (**not measured:** memory with 1,000 retained messages). B6 (connect rate) was
run and found to measure the generator. Added: the paced cell, which shows that at a load every broker can carry the question
is not throughput.

## What changed because of this

A first run of lexsys-mqtt's saturated QoS 0 cell gave about 300k deliveries/s, half of what the later runs show. Reasoning
from the shape of the cell (about 85,000 messages a second to 100 subscribers, nearly all refused by full queues, so millions
of refusals a second) found the cost: each refusal left a log event. The count stayed exact and the event is now left once
a second per rule (`tables.refuse`), the log's `suppressed` figure being derived from the counter. All 112 conformance tests
pass on the result, including the one that checks that a flood of one rule is a handful of log lines and an exact count.
