# Benchmark: where lexsys-mqtt stands

> **Status: measured once, on one shared 4-core VM, and caveated below. Not a verdict.** Cells were fixed in
> `docs/design.md` section 11 before the broker existed; what was actually run differs from that plan in ways listed
> under "Cells as run", and the design section is corrected to match. Raw results are `bench/results.json`; the harness is
> `bench/run.py`, the tables below are `python3 bench/report.py`.

## What was compared

lexsys-mqtt at `6f7c49f` (and the commits after it that touch only documents, tests and the harness), against the five brokers below,
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
| Saturated fan-out QoS 0, 1 and 2 | the same with the publishers going as fast as they can (`-I 0`; QoS 1 and 2 with 32 in flight, subscribers at the same QoS) | deliveries per second at the subscribers |
| Latency | a Python raw-socket probe: publish, wait for it at a second connection, one message in flight, 20,000 samples after 3,000 of warm-up; with 0 and with 1,000 idle connections held open | p50, p99, p99.9 in microseconds |
| Memory | the broker container's memory as `docker stats` reports it, idle, with 1,000 and with 5,000 idle subscriber connections | MiB |
| Connect | 5,000 clients, as fast as the generator goes | connections established (the rate is the generator's, see below) |

## Results

Medians, with the range of runs in brackets. Final data: `bench/results.json`.

### 1 core

| Broker | Paced QoS 0 (20k/s offered), delivered/s | Saturated QoS 0, delivered/s | Saturated QoS 1, delivered/s | Saturated QoS 2, delivered/s |
|---|---:|---:|---:|---:|
| lexsys-mqtt | 20000 (20000-20000) | 1222k (1196k-1245k) | 199.2k (197.4k-199.7k) | 112.4k (110.0k-113.6k) |
| Mosquitto 2.0.18 | 20000 (19998-20003) | 344k (324k-394k) | 174.8k (171.6k-189.8k) | 9.1k (9.1k-9.1k) |
| NanoMQ | 19997 (19995-20000) | 107k (103k-111k) | 72.0k (71.0k-72.9k) | 19.9k (19.7k-20.3k) |
| EMQX 5.8.6 | 20000 (20000-20000) | 57k (56k-60k) | 36.0k (34.5k-36.8k) | 20.2k (19.5k-20.4k) |
| VerneMQ 2.2.1 | 20000 (20000-20000) | 307k (307k-334k) | 11.0k (10.6k-11.1k) | 8.2k (7.7k-9.2k) |
| HiveMQ CE | 20010 (20007-20024) | 0k (0k-0k) | 8.4k (8.2k-8.5k) | - |

| Broker | Latency p50 / p99 / p99.9 µs, 0 idle connections | same, 1,000 idle | Memory MiB: idle / 1,000 / 5,000 connections | 5,000 connected |
|---|---|---|---|---:|
| lexsys-mqtt | 42/79/168 | 43/89/482 | 5 / 7 / 14 | 5000 |
| Mosquitto 2.0.18 | 46/84/226 | 47/87/218 | 2 / 10 / 38 | 5000 |
| NanoMQ | 66/112/357 | 70/117/182 | 1 / 14 / 68 | 5000 |
| EMQX 5.8.6 | 71/126/338 | 69/114/165 | 194 / 209 / 313 | 5000 |
| VerneMQ 2.2.1 | 71/124/208 | 93/341/778 | 79 / 106 / 238 | 5000 |
| HiveMQ CE | 161/4570/6310 | 179/4848/6249 | 232 / 249 / 291 | 5000 |

### 2 cores

| Broker | Paced QoS 0 (20k/s offered), delivered/s | Saturated QoS 0, delivered/s | Saturated QoS 1, delivered/s | Saturated QoS 2, delivered/s |
|---|---:|---:|---:|---:|
| lexsys-mqtt | 20000 (20000-20000) | 1037k (982k-1044k) | 162.6k (149.9k-166.7k) | 76.9k (74.3k-84.0k) |
| Mosquitto 2.0.18 | 20000 (19997-20000) | 380k (358k-391k) | 98.9k (98.0k-100.3k) | 9.0k (9.0k-9.0k) |
| NanoMQ | 20000 (19999-20000) | 182k (169k-184k) | 96.9k (96.1k-99.6k) | 32.9k (32.8k-35.1k) |
| EMQX 5.8.6 | 20000 (19998-20001) | 86k (85k-87k) | 55.2k (53.6k-56.5k) | 28.7k (28.4k-29.8k) |
| VerneMQ 2.2.1 | 20000 (19998-20000) | 563k (547k-596k) | 16.3k (15.6k-17.9k) | 15.5k (13.1k-15.6k) |
| HiveMQ CE | 20003 (20000-20005) | 0k (0k-0k) | 16.9k (14.9k-17.6k) | - |

| Broker | Latency p50 / p99 / p99.9 µs, 0 idle connections | same, 1,000 idle | Memory MiB: idle / 1,000 / 5,000 connections | 5,000 connected |
|---|---|---|---|---:|
| lexsys-mqtt | 43/112/594 | 44/127/369 | 5 / 7 / 14 | 5000 |
| Mosquitto 2.0.18 | 47/92/135 | 47/94/157 | 4 / 11 / 40 | 5000 |
| NanoMQ | 87/150/366 | 85/146/294 | 2 / 15 / 69 | 5000 |
| EMQX 5.8.6 | 73/180/367 | 75/177/362 | 221 / 222 / 349 | 5000 |
| VerneMQ 2.2.1 | 84/194/587 | 123/758/1415 | 335 / 351 / 453 | 5000 |
| HiveMQ CE | 161/561/4498 | 161/415/4098 | 188 / 247 / 306 | 5000 |

## Reading them

**Say these.**

- *Saturated QoS 0 fan-out:* lexsys-mqtt delivers about 1.2M/s on one core and Mosquitto about 344k/s. **But lexsys-mqtt's
  figure is a lower bound**: the broker used 63% to 66% of its core (2 cores: 15% to 17%), so the load generator, not the
  broker, was the limit (`generator_limited` in the results). The ratio is "at least 3x on this generator", not 3x.
  Mosquitto, NanoMQ, EMQX and VerneMQ ran at 90% to 100% of their cores on one core and are measured at their limits.
- *Saturated QoS 1 fan-out:* lexsys-mqtt 199k/s against Mosquitto 175k/s on one core (the broker is at 87% to 90% CPU, not
  flagged generator-limited);
  with two cores the lexsys-mqtt cells are generator-limited (42% to 44% CPU) and Mosquitto's too (36%), so no ordering is claimed.
- *Saturated QoS 2 fan-out* (added with QoS 2; one core, three runs, HiveMQ CE not run): lexsys-mqtt delivers about 112k/s at 87% to 94%
  of its core, against NanoMQ and EMQX at about 20k/s (both at 92% to 95%) and VerneMQ at about 8k/s. **Read the delivery ratio
  with it:** lexsys-mqtt's queues are bounded and drop the newest message, so only about 13% of the offered deliveries reached a
  subscriber (`delivery_ratio` in the results; NanoMQ about 31%), while EMQX, VerneMQ and Mosquitto delivered nearly all of what the
  publishers offered, because they slow the publishers instead. These are different answers to overload, and "deliveries per
  second" favours dropping. **Mosquitto's 9k/s is not its limit**: it used 15% to 17% of its core (8% on two cores) and is flagged
  generator-limited; why a QoS 2 publisher gets so little through it was not investigated, so no ordering against it is claimed.
  On two cores lexsys-mqtt's QoS 2 figure (about 77k/s) is generator-limited at 44% CPU and is lower than on one core; that is
  not explained.
- *Latency with one message in flight:* p50 42 microseconds against Mosquitto's 46 and the others' 66 to 161. **The probe is
  Python**, so a few microseconds either way is not evidence; the gap to the Erlang and JVM brokers is.
- *Memory:* see "Memory" below. An idle connection costs lexsys-mqtt about 0.3 KiB of resident size, against Mosquitto's about 1.1 KiB; at 5,000 connections `docker stats` shows 14 MiB against Mosquitto's 38.
- *Paced fan-out:* every broker delivers all 20,000 messages a second; nothing separates them.

**Do not say these.**

- *HiveMQ CE's saturated QoS 0 result of about 0 is a collapse, not a speed.* Four publishers going flat out put it into a state
  where it stops delivering and its memory climbs to about 4 GiB; its paced result is fine. Every HiveMQ run starts from a
  freshly started container for that reason.
- *No number here is a throughput limit* of a broker marked generator-limited, and "saturated" means brokers drop messages
  (all do; lexsys-mqtt counts them as `limit.queue`), so the figure is what reached the subscribers.
- *The connect cell says nothing about the brokers.* Every one connected 5,000 clients at the generator's ceiling.

## Memory

The table's figures are `docker stats` (cgroup usage). They include page cache and kernel memory and do **not** agree with the
process's resident size: for Mosquitto 5,000 connections is 38 MiB in `docker stats` and about 1.1 KiB a connection of
resident size by `smaps`; the cgroup `rss` line is 30.6 MiB. **Not reconciled**; I do not know what the cgroup counts
that the process does not. Per connection, by process resident size (`tests/conformance/test_memory.py` asserts it stays under
2 KiB): lexsys-mqtt about 0.34 KiB, Mosquitto about 1.1 KiB; idle 5.8 against 7.2 MiB.

**What changed.** Until this was fixed, lexsys-mqtt took about 8.3 KiB a connection (53 MiB at 5,000 in `docker stats`, against
Mosquitto's 38), because each connection had a fixed input buffer and a fixed session queue and touched one 4 KiB page of each
(the CONNECT bytes, the SUBACK). Now one scratch buffer takes every read, and a connection holds an input slot only while a
packet is split across reads; a session holds a queue buffer only while its queue has entries. Both come from pools whose most
recently freed slot is reused first, so the pages touched follow what is in flight, not how many connections there are. The
pools are as large as the old slabs, so the bounds and `memory_bytes` are unchanged. Throughput did not move (QoS 1 one core 199k/s
before and after; QoS 0 is generator-limited both times).

**Transparent huge pages change the unit.** The figures here were measured with THP set to `madvise`, where a table that is touched
lazily is resident by 4 KiB pages. With THP `always` (the CI runners' setting, found when a resident-size test there grew by exactly one
2 MiB huge page late in its run) the first touch of a 2 MiB-aligned part of a table makes all of it resident, so memory then grows in 2 MiB
steps at moments the workload does not choose, up to the `memory_bytes` the `listening` record states. The per-connection figures above
are therefore for THP `madvise` or `never`; **not measured** under `always`. The memory tests run the broker with THP off for the same
reason (`Broker(no_thp=True)`).

**What it does not do.** A broker with thousands of connections that all have a queued message, or all a split packet, at the
same moment touches as many pages as before; and resident size never shrinks, so a burst's pages stay. That is the same
behaviour as the tables elsewhere in the broker, and the bound is still `memory_bytes`.

## Caveats

- **One shared VM**, with noise I cannot see, and no repeat on another machine. Differences under about 10% are not claims.
- **Defaults.** Nothing was tuned for any broker. A tuned Mosquitto (queue lengths, `max_inflight_messages`), EMQX with fewer
  schedulers, or HiveMQ CE with a sized heap would move the numbers, in ways this benchmark does not measure.
- **Memory is `docker stats`**, not resident size; see "Memory" above for the disagreement, which is not resolved.
- **lexsys-mqtt runs from the load generator's image** because Docker Hub was rate-limiting a pull of a plain OS image; the
  binary needs only glibc 2.34.
- **The latency probe is not a load test**: one message in flight and nothing else running, so it measures a quiet path, not tail
  latency under load.
- **The machine was not always otherwise idle.** While the incumbents' runs were in progress I sometimes compiled and ran
  tests on the same VM, and one of lexsys-mqtt's early runs was disturbed that way and replaced. lexsys-mqtt's reported runs
  were made with the machine idle; the incumbents' were not always. Medians and ranges are shown, and small differences
  should be read with that in mind.
- **Effort was uneven.** lexsys-mqtt's saturated QoS 0 path was profiled and changed twice (a log event per refused message became a counter; per-message writes became one coalesced write per flush, below). No other broker was tuned. No other broker was tuned.

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

Issue #12, the bimodality: the earlier runs flipped between about 350k and 650k deliveries/s. Controlled placement of the load
generator, with kernel time, context switches and inter-processor interrupts measured, showed the flip came from where the
generator's threads ran relative to the broker: with a write per delivered message, each write woke a subscriber and that
kernel cost dominated. The fix is write coalescing: a flush stages the queued messages in one 64 KiB buffer and does one
write per connection. After it the cell no longer flips, and the broker uses 60% to 68% of its core, so the cell is now
bounded by the generator. The earlier "650k" fast mode was also generator-limited, so it was never the broker's limit.
**Not explained:** the QoS 1 two-core spread (114k to 160k) and the roughly 10% lower result in one placement variant after
coalescing.
