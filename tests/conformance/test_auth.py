"""Authentication (design section 7c): the three refusals and their CONNACK codes, that a refused login changes
nothing, the two schemes, the cost budget, a bad table, and what is never logged.
"""

import json
import random
import subprocess
import time
import unittest

from harness import BINARY, Broker, Client, Closed, PINGREQ, connect_packet, free_port, key_line, pkt, run, records, s16, user_line

ALICE = "correct horse"
KEY = "0123456789abcdef0123456789abcdef"


def table():
    return user_line("alice", ALICE) + key_line("dev-1", KEY) + user_line("bob", "hunter2", iterations=2000)


class Auth(unittest.TestCase):
    def setUp(self):
        self.broker = Broker(users=table())
        self.addCleanup(self.broker.__exit__, None, None, None)

    def client(self, **kw):
        c = self.broker.client(**kw)
        self.addCleanup(c.close)
        return c

    def refused(self, code, rule, **creds):
        c = self.client()
        c.connect("who", expect_code=code, **creds)
        with self.assertRaises(Closed):
            c.recv(2)
        self.assertEqual(len(self.broker.events("refusal", rule, wait=2)) >= 1, True)

    # ---- the three rows of the table ---------------------------------------------------------------------------

    def test_no_user_name_is_not_authorised(self):
        # CONNACK 5 (not authorised), and the connection is closed.
        self.refused(5, "auth.required")

    def test_a_wrong_password_is_bad_credentials(self):
        self.refused(4, "auth.bad-credentials", username="alice", password="nope")

    def test_an_unknown_user_gets_the_same_answer_as_a_wrong_password(self):
        # Neither the code nor the rule says which names exist.
        self.refused(4, "auth.bad-credentials", username="mallory", password=ALICE)

    def test_a_user_name_with_no_password_is_bad_credentials(self):
        self.refused(4, "auth.bad-credentials", username="alice")

    def test_an_empty_password_is_bad_credentials(self):
        self.refused(4, "auth.bad-credentials", username="alice", password="")

    def test_the_right_password_connects_and_works(self):
        sub = self.client()
        sub.connect("s", username="alice", password=ALICE)
        sub.subscribe("a/#")
        pub = self.client()
        pub.connect("p", username="bob", password="hunter2")
        pub.publish("a/b", b"hello")
        self.assertEqual(sub.recv_publish(2)[0], "a/b")

    def test_the_key_scheme_connects_with_the_secret_only(self):
        c = self.client()
        c.connect("k", username="dev-1", password=KEY)
        c = self.client()
        c.connect("k2", expect_code=4, username="dev-1", password=KEY[:-1])

    def test_the_user_name_is_case_sensitive(self):
        self.refused(4, "auth.bad-credentials", username="Alice", password=ALICE)

    # ---- a refused login changes nothing -----------------------------------------------------------------------

    def test_a_refused_login_cannot_take_over_a_session_or_fire_its_will(self):
        watcher = self.client()
        watcher.connect("w", username="bob", password="hunter2")
        watcher.subscribe("wills/#")
        victim = self.client()
        victim.connect("victim", username="alice", password=ALICE, will=("wills/victim", b"gone", 0, False))
        # The attacker uses the victim's client identifier and the wrong password.
        attacker = self.client()
        attacker.connect("victim", expect_code=4, username="alice", password="wrong")
        with self.assertRaises(Closed):
            attacker.recv(2)
        # The victim is still connected, and no will was published.
        victim.send(PINGREQ)
        victim.expect(13)
        with self.assertRaises(TimeoutError):
            watcher.recv_publish(0.5)

    def test_a_refused_login_creates_no_session(self):
        c = self.client()
        c.connect("ghost", expect_code=4, username="alice", password="wrong", clean=False)
        c = self.client()
        present = c.connect("ghost", username="alice", password=ALICE, clean=False)
        self.assertEqual(present, 0)

    # ---- what is never logged ----------------------------------------------------------------------------------

    def test_neither_user_names_nor_passwords_reach_the_log(self):
        c = self.client()
        c.connect("x", expect_code=4, username="private-user-name", password="private-pass-word")
        c = self.client()
        c.connect("y", username="alice", password=ALICE)
        time.sleep(0.3)
        status, records = self.broker.stop()
        text = json.dumps(records)
        for secret in ("private-user-name", "private-pass-word", ALICE, KEY, "hunter2"):
            self.assertNotIn(secret, text)

    def test_the_refusals_are_counted_by_rule(self):
        for creds in ({}, {"username": "alice", "password": "x"}, {"username": "zed", "password": "x"}):
            c = self.client()
            c.connect("c", expect_code=5 if not creds else 4, **creds)
        time.sleep(0.3)
        _, records = self.broker.stop()
        end = [r for r in records if r.get("type") == "stats"][-1]
        self.assertEqual(end["refusals"]["auth.required"], 1)
        self.assertEqual(end["refusals"]["auth.bad-credentials"], 2)


class Open(unittest.TestCase):
    def test_without_the_flag_user_names_and_passwords_are_ignored(self):
        with Broker() as b:
            c = b.client()
            c.connect("c", username="anyone", password="anything")
            c = b.client()
            c.connect("d")


class Budget(unittest.TestCase):
    def test_logins_over_the_budget_are_refused_with_connack_3(self):
        # alice costs 1,000 iterations; a budget of 3,000 admits three in one second.
        users = user_line("alice", ALICE, iterations=1000) + key_line("dev-1", KEY)
        with Broker("--auth-budget", "3000", users=users) as b:
            codes = []
            for i in range(10):
                c = b.client()
                c.send(connect_packet("c%d" % i, username="alice", password=ALICE))
                _, body = c.expect(2)
                codes.append(body[1])
                c.close()
            self.assertIn(3, codes)
            self.assertIn(0, codes)
            self.assertTrue(b.events("refusal", "limit.auth-budget", wait=2))

    def test_a_record_dearer_than_the_whole_budget_can_still_log_in(self):
        # The first login of a second is always done.
        with Broker("--auth-budget", "100", users=user_line("alice", ALICE, iterations=1000)) as b:
            time.sleep(1.1)
            c = b.client()
            c.connect("c", username="alice", password=ALICE)

    def test_keys_cost_one_unit_each(self):
        with Broker("--auth-budget", "50", users=key_line("dev-1", KEY)) as b:
            for i in range(40):
                c = b.client()
                c.connect("c%d" % i, username="dev-1", password=KEY)
                c.close()


class Table(unittest.TestCase):
    def run_broker(self, users, *flags):
        p = subprocess.run([BINARY, "serve", "--port", str(free_port()), "--auth", "stdin", *flags], input=users, capture_output=True,
                           timeout=15)
        return p.returncode, p.stdout.decode()

    def test_a_bad_table_refuses_to_start_naming_the_line_and_never_its_content(self):
        marker = "ZZZMARKERZZZ"
        rc, out = self.run_broker((user_line("a", "p") + "b:%s\n" % marker).encode())
        self.assertEqual(rc, 2)
        err = json.loads(out.splitlines()[0])["error"]
        self.assertEqual(err["rule"], "auth.credentials-invalid")
        self.assertEqual(err["code"], "INVALID_ARGS")
        self.assertEqual(err["detail"], {"reason": "bad-scheme", "line": 2})
        self.assertNotIn(marker, out)
        self.assertNotIn("listening", out)

    def test_every_reason_is_named(self):
        good = user_line("a", "p")
        h = "0" * 64
        s = "00" * 16
        cases = {
            "too-many-users": (good + user_line("b", "p"), ["--auth-users", "1"]),
            "line-too-long": ("a:" + "x" * 300 + "\n", []),
            "bad-user-name": (":sha256$%s$%s\n" % (s, h), []),
            "bad-scheme": ("a:md5$%s$%s\n" % (s, h), []),
            "bad-iterations": ("a:pbkdf2-sha256$0$%s$%s\n" % (s, h), []),
            "bad-salt": ("a:sha256$00$%s\n" % h, []),
            "bad-hash": ("a:sha256$%s$00\n" % s, []),
            "duplicate-user": (good + good, []),
            "missing-separator": ("nocolon\n", []),
        }
        for reason, (text, flags) in cases.items():
            rc, out = self.run_broker(text.encode(), *flags)
            self.assertEqual(rc, 2, reason)
            self.assertEqual(json.loads(out.splitlines()[0])["error"]["detail"]["reason"], reason)

    def test_input_beyond_the_table_bound_is_refused(self):
        rc, out = self.run_broker(b"x" * 600, "--auth-users", "2")
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(out.splitlines()[0])["error"]["detail"]["reason"], "input-too-large")

    def test_an_empty_table_starts_and_refuses_everyone(self):
        with Broker(users="") as b:
            c = b.client()
            c.connect("c", expect_code=4, username="a", password="b")

    def test_a_big_table_loads_and_the_last_user_logs_in(self):
        users = "".join(key_line("user-%d" % i, "secret-%d" % i) for i in range(2000))
        t0 = time.time()
        with Broker("--auth-users", "2000", users=users) as b:
            started = time.time() - t0
            c = b.client()
            c.connect("c", username="user-1999", password="secret-1999")
            self.assertLess(started, 5)

    def test_the_table_is_not_in_the_listening_record_or_introspect(self):
        users = user_line("private-user-name", "p")
        with Broker(users=users) as b:
            b.drain()
            self.assertNotIn("private-user-name", json.dumps(b.lines))
        out = subprocess.run([BINARY, "introspect"], capture_output=True, timeout=15).stdout.decode()
        self.assertIn("auth-budget", out)
        self.assertNotIn("private-user-name", out)


class Fuzz(unittest.TestCase):
    """CONNECT packets with hostile user name and password fields: no input reaches a trap (design section 7c)."""

    def test_random_credential_fields_never_hurt_the_broker(self):
        rng = random.Random(7)
        with Broker(users=table()) as b:
            for case in range(300):
                user = bytes(rng.randrange(256) for _ in range(rng.choice([0, 1, 5, 64, 65, 300])))
                password = bytes(rng.randrange(256) for _ in range(rng.choice([0, 1, 32, 1000])))
                good = rng.random() < 0.15
                if good:
                    user, password = b"alice", ALICE.encode()
                body = s16(b"MQTT") + bytes([4]) + bytes([0xC2]) + (60).to_bytes(2, "big") + s16("f%d" % case) + \
                    len(user).to_bytes(2, "big") + user + len(password).to_bytes(2, "big") + password
                raw = pkt(0x10, body)
                if rng.random() < 0.2:
                    raw = raw[:rng.randrange(1, len(raw))]
                try:
                    c = Client(b.port)
                    c.send(raw)
                    try:
                        c.recv(0.3)
                    except (Closed, TimeoutError):
                        pass
                    c.close()
                except OSError:
                    pass
            self.assertTrue(b.alive())
            c = b.client()
            c.connect("after", username="alice", password=ALICE)
            c.send(PINGREQ)
            c.expect(13)
            c.close()
            status, lines = b.stop()
            self.assertEqual(status, 0)
            status, out, _ = run("rules")
            catalogue = {r["tag"] for r in records(out) if r["type"] == "rule"}
            self.assertLessEqual({r["rule"] for r in lines if r.get("type") == "refusal"}, catalogue)


if __name__ == "__main__":
    unittest.main()
