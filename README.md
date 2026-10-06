# lexsys-mqtt

An MQTT 3.1.1 broker written in [lex-sys](https://github.com/alpibrusl/lex-sys): no `Ffi`, no `unsafe`, and a checkable
authority report. The model is [`lexsys-cache`](https://github.com/alpibrusl/lexsys-cache): one thread with one poller,
memory sized at start, a binary protocol parsed with bounds, and measurements against the incumbent (Mosquitto) fixed
before the code.

**Status: design proposed (`docs/design.md`), scaffold only. No broker yet.** The plan and its tasks are in the epic issue. The first deliverable is
`docs/design.md`: scope, the authority row, the gates, written before any code.

## Intended scope (v1)

MQTT 3.1.1 over plain TCP: CONNECT/CONNACK, keepalive, clean sessions, will messages, SUBSCRIBE with `+` and `#`
wildcards, PUBLISH at QoS 0 and 1, retained messages, bounded per-subscriber queues. Not in v1: QoS 2, persistence,
TLS (it needs foreign code and would make the authority report unbounded), MQTT 5, websockets, clustering.

## Why a repository of its own

The cache's claim is that it never touches the filesystem. A broker that may persist or log would change that claim,
and in this toolbox one program carries one authority row. The server skeleton is shared by pattern, not by code, until
a second user shows what is worth extracting.
