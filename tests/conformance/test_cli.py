"""The agent-first surface (design section 5a, gate G9): what a program reading
`mqtt` can rely on without reading prose."""

import json
import pathlib
import re
import signal
import subprocess
import time
import unittest

import jsonschema

from harness import BINARY, ROOT, Broker, connect_packet, free_port, records, run

SCHEMA = json.loads((ROOT / "schemas" / "mqtt.v1.json").read_text())
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)


def valid(record):
    errors = list(VALIDATOR.iter_errors(record))
    assert not errors, "%s: %s" % (record, errors[0].message)


def design_bounds():
    """(flag, default, ceiling) of every row of docs/design.md section 4's table."""
    text = (ROOT / "docs" / "design.md").read_text()
    section = text[text.index("## 4. Memory"):text.index("## 5. Rules")]
    rows = []
    for line in section.splitlines():
        m = re.match(r"\| .* \| `([a-z0-9-]+)` \| ([\d,]+) \| ([\d,]+) \|", line)
        if m:
            rows.append((m.group(1), int(m.group(2).replace(",", "")), int(m.group(3).replace(",", ""))))
    return rows


class Surface(unittest.TestCase):
    def introspect(self):
        status, out, err = run("introspect")
        self.assertEqual(status, 0)
        self.assertEqual(err, "")
        return json.loads(out)

    def test_introspect_is_one_json_document_with_the_authority(self):
        doc = self.introspect()
        self.assertEqual(doc["tool"], "mqtt")
        self.assertEqual(doc["output"], "stream")
        self.assertTrue(doc["authority"]["bounded"])
        self.assertEqual(doc["authority"]["foreign_symbols"], [])
        names = {l["name"] for l in doc["authority"]["labels"]}
        for forbidden in ("ffi", "net_out", "file_read", "file_write", "fs_read", "fs_write", "io_read", "dir_read"):
            self.assertNotIn(forbidden, names)
        self.assertEqual(doc["reads_environment"], False)

    def test_introspect_agrees_with_the_committed_authority_report(self):
        committed = json.loads((ROOT / "manifests" / "mqtt.authority.json").read_text())
        self.assertEqual(self.introspect()["authority"], committed)

    def test_the_flag_table_is_design_section_4(self):
        doc = self.introspect()
        flags = {f["name"].lstrip("-"): f for f in doc["flags"]}
        limits = {l["name"]: l for l in doc["limits"]}
        rows = design_bounds()
        self.assertGreaterEqual(len(rows), 17)
        for name, default, ceiling in rows:
            self.assertIn(name, flags, "design names a flag the binary does not have: " + name)
            self.assertEqual(int(flags[name]["default"]), default, name)
            self.assertEqual(limits[name]["default"], default, name)
            self.assertEqual(limits[name]["ceiling"], ceiling, name)
        # Every numeric flag is either a bound in the table or one of the two the text names.
        extra = set(flags) - {r[0] for r in rows}
        self.assertEqual(extra, {"port", "stats-seconds", "format"})

    def test_every_flag_is_harmless_to_authority(self):
        for f in self.introspect()["flags"]:
            self.assertEqual(f["role"], "none", f["name"])

    def test_skill_is_generated_from_the_same_table(self):
        status, out, err = run("skill")
        self.assertEqual(status, 0)
        self.assertTrue(out.startswith("---\nname: mqtt"))
        for f in self.introspect()["flags"]:
            self.assertIn(f["name"], out)

    def test_rules_command_lists_every_connection_rule(self):
        status, out, err = run("rules")
        self.assertEqual(status, 0)
        recs = records(out)
        for r in recs:
            valid(r)
        self.assertEqual(recs[-1]["type"], "end")
        self.assertTrue(recs[-1]["complete"])
        schema_rules = SCHEMA["$defs"]["rule"]["properties"]["tag"]["enum"]
        self.assertEqual([r["tag"] for r in recs[:-1]], schema_rules)

    # ---- refusals of the process ----------------------------------------

    def refused(self, *args, rule, status):
        code, out, err = run(*args)
        self.assertEqual(code, status)
        self.assertEqual(err, "")
        recs = records(out)
        for r in recs:
            valid(r)
        self.assertEqual(recs[-1]["type"], "end")
        self.assertFalse(recs[-1]["ok"])
        self.assertFalse(recs[-1]["complete"])
        errors = [r["error"] for r in recs if r["type"] == "error"]
        self.assertEqual(errors[0]["rule"], rule)
        self.assertEqual(recs[-1]["errors"], len(errors))
        return errors

    def test_no_command(self):
        self.refused(rule="args.missing-operand", status=2)

    def test_unknown_command(self):
        self.refused("frobnicate", rule="args.unknown-command", status=2)

    def test_unknown_flag_offers_the_nearest_as_a_repair(self):
        errors = self.refused("serve", "--prot", "1883", rule="args.unknown-flag", status=2)
        self.assertEqual(errors[0]["detail"]["nearest"], "--port")
        self.assertEqual(len(errors), 1, "an unknown flag is not also reported as a stray operand")
        self.assertEqual(errors[0]["repair"]["kind"], "retry")

    def test_missing_value_duplicate_and_bad_value(self):
        self.refused("serve", "--port", rule="args.missing-value", status=2)
        self.refused("serve", "--port", "1", "--port", "2", rule="args.duplicate-flag", status=2)
        self.refused("serve", "--port", "http", rule="args.bad-value", status=2)

    def test_out_of_range_is_repaired_to_the_nearest_legal_value(self):
        errors = self.refused("serve", "--max-connections", "99999999", "--inflight", "0",
                              rule="args.out-of-range", status=2)
        self.assertEqual(len(errors), 2)
        high, low = errors
        self.assertEqual(high["detail"]["ceiling"], 16384)
        self.assertEqual(high["repair"]["argv"][3], "16384")
        self.assertEqual(low["detail"]["minimum"], 1)
        self.assertEqual(low["repair"]["argv"][5], "1")

    def test_a_repair_never_goes_past_the_ceiling(self):
        for flag, value in (("--max-packet", "999999999"), ("--topic-levels", "1000")):
            errors = self.refused("serve", flag, value, rule="args.out-of-range", status=2)
            repaired = errors[0]["repair"]["argv"]
            self.assertEqual(repaired[3], str(errors[0]["detail"]["ceiling"]))

    def test_applying_a_repair_by_script_gets_a_running_broker(self):
        port = free_port()
        errors = self.refused("serve", "--port", str(port), "--max-connections", "99999999",
                              rule="args.out-of-range", status=2)
        argv = [BINARY if a == errors[0]["repair"]["argv"][0] else a for a in errors[0]["repair"]["argv"]]
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            first = json.loads(proc.stdout.readline())
            self.assertEqual(first["type"], "listening")
            self.assertEqual(first["max_connections"], 16384)
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=10)
            proc.stdout.close()
            proc.stderr.close()

    def test_queue_must_hold_a_packet(self):
        self.refused("serve", "--max-packet", "4096", "--queue-bytes", "4096", rule="args.conflict", status=2)

    def test_port_in_use_is_a_conflict_with_no_script_repair(self):
        with Broker() as broker:
            errors = self.refused("serve", "--port", str(broker.port), rule="conflict.address-in-use", status=5)
            self.assertEqual(errors[0]["code"], "CONFLICT")
            self.assertEqual(errors[0]["repair"]["kind"], "none")
            self.assertEqual(errors[0]["detail"]["port"], broker.port)

    def test_exit_codes_in_introspect_are_the_ones_used(self):
        doc = self.introspect()
        table = {r["rule"]: r["exit"] for r in doc["rules"]}
        self.assertEqual(table["args.unknown-flag"], 2)
        self.assertEqual(table["conflict.address-in-use"], 5)
        self.assertEqual(table["io.permission-denied"], 4)

    # ---- the log ---------------------------------------------------------

    def test_a_graceful_stop_ends_the_stream_with_an_end_record(self):
        broker = Broker("--stats-seconds", "1")
        with broker:
            c = broker.client()
            c.connect("a")
            c.subscribe("t")
            c.publish("t", b"x")
            c.recv_publish()
            time.sleep(1.2)
            status, lines = broker.stop()
        self.assertEqual(status, 0)
        for r in lines:
            valid(r)
        self.assertEqual(lines[0]["type"], "listening")
        self.assertEqual(lines[-1]["type"], "end")
        self.assertTrue(lines[-1]["ok"] and lines[-1]["complete"])
        self.assertEqual(lines[-2]["type"], "stats")
        self.assertEqual(lines[-1]["publishes"], 1)
        self.assertEqual(lines[-1]["delivered"], 1)
        self.assertEqual(lines[-1]["connects"], 1)
        self.assertGreaterEqual(len([r for r in lines if r["type"] == "stats"]), 2)

    def test_sigint_also_stops_it_cleanly(self):
        with Broker() as broker:
            broker.proc.send_signal(signal.SIGINT)
            broker.proc.wait(timeout=10)
            self.assertEqual(broker.proc.returncode, 0)

    def test_no_message_payload_is_ever_logged(self):
        with Broker() as broker:
            c = broker.client()
            c.connect("a")
            c.subscribe("topic/secret")
            c.publish("topic/secret", b"PAYLOAD-MARKER-42")
            c.recv_publish()
            c.send(b"\xc0\x00")
            c.close()
            time.sleep(0.2)
            status, lines = broker.stop()
        self.assertNotIn("PAYLOAD-MARKER-42", json.dumps(lines))

    def test_refusals_are_rate_limited_and_counted(self):
        with Broker() as broker:
            # 200 clients each break the same rule within about a second.
            for i in range(200):
                c = broker.client()
                c.send(b"\xc0\x00")
                c.close()
            time.sleep(1.5)
            status, lines = broker.stop()
        for r in lines:
            valid(r)
        refusals = [r for r in lines if r["type"] == "refusal"]
        self.assertLess(len(refusals), 20, "refusal records are bounded per second")
        self.assertGreater(sum(r["suppressed"] for r in refusals) + len(refusals), 150)
        stats = [r for r in lines if r["type"] == "stats"][-1]
        self.assertEqual(stats["refusals"]["protocol.connect-first"], 200)

    def test_client_ids_are_text_or_base64_and_cut_short(self):
        with Broker() as broker:
            c = broker.client()
            c.connect("x" * 100)
            c.send(connect_packet("again"))
            self.assertTrue(c.closed())
            events = broker.events("refusal", "protocol.connect-twice", wait=3)
            status, lines = broker.stop()
        self.assertEqual(events[0]["client_id"], "x" * 32)
        self.assertTrue(events[0]["truncated"])

    def test_the_log_is_deterministic_apart_from_time(self):
        def one():
            with Broker(port=free_port()) as broker:
                c = broker.client()
                c.send(b"\xc0\x00")
                c.close()
                broker.events("refusal", wait=3)
                status, lines = broker.stop()
            out = []
            for r in lines:
                r = dict(r)
                r.pop("t_ms", None)
                r.pop("port", None)
                out.append(json.dumps(r, sort_keys=False))
            return out
        self.assertEqual(one(), one())


class Text(unittest.TestCase):
    def test_text_mode_writes_sentences_to_stderr_and_nothing_to_stdout(self):
        status, out, err = run("serve", "--format", "text", "--prot", "1")
        self.assertEqual(status, 2)
        self.assertEqual(out, "")
        self.assertIn("args.unknown-flag", err)

    def test_text_mode_rules_are_lines_not_json(self):
        status, out, err = run("rules", "--format", "text")
        self.assertEqual(status, 0)
        lines = out.splitlines()
        self.assertEqual(len(lines), 28)
        self.assertTrue(lines[0].startswith("protocol.connect-first  "))
        self.assertNotIn("{", out)

    def test_text_mode_log_is_key_value_lines_with_an_end_line(self):
        port = free_port()
        proc = subprocess.Popen([BINARY, "serve", "--format", "text", "--port", str(port)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(proc.stdout.readline().startswith("listening port=%d " % port))
            from harness import Client
            c = Client(port)
            c.send(b"\xc0\x00")
            c.close()
            time.sleep(0.4)
            proc.send_signal(signal.SIGTERM)
            rest, err = proc.communicate(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
        lines = [l for l in rest.splitlines() if l]
        self.assertTrue(lines[-1].startswith("end ok=true complete=true"))
        self.assertTrue(any(l.startswith("refusal rule=protocol.connect-first") for l in lines))
        self.assertNotIn("{", rest)


if __name__ == "__main__":
    unittest.main()
