"""Authentication against Mosquitto 2.0.18 (design section 7c): the same logins sent to this broker and to Mosquitto with
the same user, and what each answers is compared. Where they differ the difference is written down in `CASES` and asserted
as a difference, like `KNOWN_DIFFERENCES` in test_differential.py. `scripts/side_by_side.py` renders these cases into
docs/side-by-side.md.

Seen, and why this broker differs: Mosquitto answers CONNACK 5 (not authorised) to every login that is not accepted,
including a wrong password, an unknown user and a missing password. MQTT 3.1.1 (3.2.2.3) has a code for those, 4 (bad user
name or password), and this broker uses it; the code 5 stays for a client that sent no user name at all.
"""

import unittest

from harness import Broker, Client, Closed, PINGREQ, connect_packet, user_line
from test_differential import MOSQUITTO, MosquittoBroker

USER = "alice"
PASSWORD = "secret"

# label: (what the client sends, this broker's answer, Mosquitto's answer). An answer is (CONNACK code, connection after).
OK = (0, "open")
CLOSED = lambda code: (code, "closed")
CASES = {
    "no user name": ({}, CLOSED(5), CLOSED(5)),
    "right user and password": ({"username": USER, "password": PASSWORD}, OK, OK),
    "wrong password": ({"username": USER, "password": "nope"}, CLOSED(4), CLOSED(5)),
    "unknown user": ({"username": "zed", "password": PASSWORD}, CLOSED(4), CLOSED(5)),
    "user name, no password": ({"username": USER}, CLOSED(4), CLOSED(5)),
    "empty password": ({"username": USER, "password": ""}, CLOSED(4), CLOSED(5)),
    "empty user name": ({"username": "", "password": PASSWORD}, CLOSED(4), CLOSED(5)),
}


def probe(port, creds):
    """One CONNECT; the CONNACK code and whether the broker then keeps the connection."""
    c = Client(port)
    try:
        c.send(connect_packet("probe", **creds))
        _, _, body = c.recv(3)
        try:
            c.recv(0.5)
        except Closed:
            return (body[1], "closed")
        except TimeoutError:
            return (body[1], "open")
        return (body[1], "open")
    finally:
        c.close()


def takeover(port):
    """A second connection with the first one's client identifier and a wrong password: the code it gets, whether the
    first connection survives, and whether the first one's will was published."""
    watcher = Client(port)
    watcher.connect("watcher", username=USER, password=PASSWORD)
    watcher.subscribe([("wills/#", 0)])
    first = Client(port)
    first.send(connect_packet("victim", username=USER, password=PASSWORD, will=("wills/victim", b"gone", 0, False)))
    first.expect(2)
    second = Client(port)
    second.send(connect_packet("victim", username=USER, password="wrong"))
    _, _, body = second.recv(3)
    code = body[1]
    second.close()
    first.send(PINGREQ)
    try:
        first.expect(13, timeout=2)
        alive = True
    except (Closed, TimeoutError):
        alive = False
    try:
        watcher.recv(0.5)
        will = True
    except TimeoutError:
        will = False
    for c in (watcher, first):
        c.close()
    return code, alive, will


@unittest.skipUnless(MOSQUITTO, "mosquitto is not installed")
class AuthDifferential(unittest.TestCase):
    def setUp(self):
        self.mine = Broker(users=user_line(USER, PASSWORD))
        self.theirs = MosquittoBroker(users=[(USER, PASSWORD)])
        self.addCleanup(self.mine.__exit__, None, None, None)
        self.addCleanup(self.theirs.stop)

    def test_every_login_is_answered_as_written_down(self):
        for label, (creds, mine, theirs) in CASES.items():
            self.assertEqual(probe(self.mine.port, creds), mine, "this broker, %s" % label)
            self.assertEqual(probe(self.theirs.port, creds), theirs, "Mosquitto, %s" % label)

    def test_a_refused_login_takes_nothing_over_in_either(self):
        # The codes differ (4 and 5); what matters agrees: the first connection lives and its will is not published.
        mine = takeover(self.mine.port)
        theirs = takeover(self.theirs.port)
        self.assertEqual(mine, (4, True, False))
        self.assertEqual(theirs, (5, True, False))


if __name__ == "__main__":
    unittest.main()
