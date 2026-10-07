#!/usr/bin/env python3
"""Write schemas/mqtt.v1.json: one JSON Schema (Draft 2020-12) for every line the
broker writes on standard output (design section 5a).

The shared parts -- the error object, the repair kinds, `text_or_bytes` -- are the
toolbox's (cancho-tools scripts/schemas.py), written out here so the file is
self-contained: it is embedded in the binary by scripts/manifest.py and printed by
`mqtt introspect`. Every object is `additionalProperties: false` except an error's
`detail`, which is rule-specific data.

    python3 scripts/schemas.py           # write
    python3 scripts/schemas.py --check   # exit 1 if the committed file differs
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIALECT = "https://json-schema.org/draft/2020-12/schema"
NAT = {"type": "integer", "minimum": 0}
CODES = ["GENERAL_ERROR", "INVALID_ARGS", "NOT_FOUND", "PERMISSION_DENIED",
         "CONFLICT", "PRECONDITION_FAILED"]

# The connection-level rules, spelled once here for the schema's `refusals` object
# and checked against `mqtt rules` by tests/conformance.
RULES = [
    "protocol.connect-first", "protocol.connect-twice", "timeout.connect", "protocol.bad-name",
    "protocol.unsupported-level", "protocol.client-id-rejected", "protocol.reserved-flags",
    "protocol.remaining-length", "limit.packet-size", "protocol.malformed-packet",
    "protocol.topic-invalid", "protocol.filter-invalid", "protocol.packet-id",
    "protocol.qos3",
    "limit.subscriptions-per-client", "limit.subscriptions-total", "limit.connections",
    "timeout.keepalive", "timeout.write-stalled", "limit.queue", "limit.retained",
    "limit.offline-sessions", "protocol.unexpected-packet", "limit.topic-level",
    "limit.will-size", "limit.output-full", "limit.qos2-inbound", "protocol.reserved-topic",
    "auth.required", "auth.bad-credentials", "limit.auth-budget",
]


def obj(props, required=None):
    return {
        "type": "object",
        "properties": props,
        "required": list(props) if required is None else required,
        "additionalProperties": False,
    }


def common_defs():
    return {
        "text_or_bytes": {
            "description": "UTF-8 text as a string; other bytes as base64 (RFC 4648 section 4, padded).",
            "oneOf": [
                {"type": "string"},
                obj({"b64": {"type": "string", "pattern": "^[A-Za-z0-9+/]*={0,2}$"}}),
            ],
        },
        "repair": {
            "oneOf": [
                {"type": "null"},
                obj({"kind": {"const": "retry"},
                     "argv": {"type": "array", "items": {"type": "string"}, "minItems": 1}}),
                obj({"kind": {"const": "none"}, "reason": {"type": "string"}}),
                obj({"kind": {"const": "choose"},
                     "options": {"type": "array", "items": obj(
                         {"argv": {"type": "array", "items": {"type": "string"}}})}}),
            ]
        },
        "error": obj({
            "code": {"enum": CODES},
            "rule": {"type": "string", "pattern": "^[a-z]+\\.[a-z0-9-]+$"},
            "message": {"type": "string"},
            "hint": {"type": ["string", "null"]},
            "repair": {"$ref": "#/$defs/repair"},
            "detail": {"type": "object"},
        }),
    }


TB = {"$ref": "#/$defs/text_or_bytes"}
TOTALS = {
    "publishes": NAT, "delivered": NAT, "connects": NAT, "disconnects": NAT,
    "retained_sent": NAT, "offline_dropped": NAT, "takeovers": NAT,
}


def schema():
    defs = common_defs()
    defs["listening"] = obj({
        "type": {"const": "listening"}, "port": NAT, "max_connections": NAT, "max_packet": NAT,
        "memory_bytes": NAT, "t_ms": NAT,
    })
    defs["refusal"] = obj({
        "type": {"const": "refusal"}, "rule": {"enum": RULES}, "connection": {"type": "integer"},
        "client_id": {"oneOf": [{"type": "null"}, TB]}, "truncated": {"type": "boolean"},
        "t_ms": NAT, "suppressed": NAT,
    })
    defs["stats"] = obj({
        "type": {"const": "stats"}, "t_ms": NAT, "connections": NAT, "sessions_online": NAT,
        "sessions_offline": NAT, "subscriptions": NAT, "retained": NAT, **TOTALS,
        "refusals": obj({r: NAT for r in RULES}),
    })
    defs["rule"] = obj({"type": {"const": "rule"}, "tag": {"enum": RULES}, "action": {"type": "string"}})
    defs["error_record"] = obj({"type": {"const": "error"}, "error": {"$ref": "#/$defs/error"}})
    end = {
        "type": {"const": "end"}, "ok": {"type": "boolean"}, "command": {"const": "mqtt"},
        "schema": {"const": "mqtt.v1"}, "complete": {"type": "boolean"},
        "t_ms": NAT, **TOTALS, "errors": NAT, "rules": NAT,
    }
    defs["end"] = obj(end, required=["type", "ok", "command", "schema", "complete"])
    one_of = [{"$ref": "#/$defs/" + n} for n in ("listening", "refusal", "stats", "rule", "error_record", "end")]
    return {
        "$schema": DIALECT,
        "$id": "https://github.com/alpibrusl/cancho-mqtt/schemas/mqtt.v1.json",
        "title": "mqtt.v1",
        "description": "One line of the NDJSON stream `mqtt` writes. The last line of a stream that finished is the end record; a stream without one was cut short.",
        "oneOf": one_of,
        "$defs": defs,
    }


def render():
    return json.dumps(schema(), indent=2, sort_keys=False) + "\n"


def main():
    path = ROOT / "schemas" / "mqtt.v1.json"
    text = render()
    if "--check" in sys.argv[1:]:
        if not path.exists() or path.read_text() != text:
            print("stale schema (run scripts/schemas.py): schemas/mqtt.v1.json")
            return 1
        return 0
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
