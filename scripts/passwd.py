#!/usr/bin/env python3
"""Make one line of the credential table `mqtt serve --auth stdin` reads (docs/design.md section 7c).

    python3 scripts/passwd.py alice >> users.txt                      # password from the terminal, or from standard input
    printf '%s' "$PASSWORD" | python3 scripts/passwd.py alice >> users.txt
    python3 scripts/passwd.py --key sensor-17 >> users.txt            # a random 128-bit device key, shown once on stderr
    mqtt serve --auth stdin < users.txt

The broker has no randomness and no file access, so this lives outside it. The password is never taken from the
command line (it would be in `ps`); standard input is read to its end and a final newline is dropped.

    --iterations N   PBKDF2-HMAC-SHA-256 iterations (default 10000; the broker refuses more than 1,000,000)
    --key            a generated secret and the one-hash `sha256` scheme, for programs, not for people
"""

import argparse
import getpass
import hashlib
import os
import secrets
import sys

MAX_NAME = 64


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name")
    ap.add_argument("--iterations", type=int, default=10000)
    ap.add_argument("--key", action="store_true")
    a = ap.parse_args()
    if not 1 <= len(a.name.encode()) <= MAX_NAME or ":" in a.name or any(ord(c) < 32 or ord(c) == 127 for c in a.name):
        sys.exit("passwd: a user name is 1 to %d bytes, without ':' or a control character" % MAX_NAME)
    salt = os.urandom(16)
    if a.key:
        secret = secrets.token_hex(16)
        print("secret for %s (shown once): %s" % (a.name, secret), file=sys.stderr)
        digest = hashlib.sha256(salt + secret.encode()).digest()
        print("%s:sha256$%s$%s" % (a.name, salt.hex(), digest.hex()))
        return
    if not 1 <= a.iterations <= 1000000:
        sys.exit("passwd: iterations are 1 to 1000000")
    if sys.stdin.isatty():
        password = getpass.getpass("password for %s: " % a.name)
        if getpass.getpass("again: ") != password:
            sys.exit("passwd: the two passwords differ")
        password = password.encode()
    else:
        password = sys.stdin.buffer.read()
        if password.endswith(b"\n"):
            password = password[:-1]
    if not password:
        sys.exit("passwd: an empty password is refused")
    digest = hashlib.pbkdf2_hmac("sha256", password, salt, a.iterations, 32)
    print("%s:pbkdf2-sha256$%d$%s$%s" % (a.name, a.iterations, salt.hex(), digest.hex()))


if __name__ == "__main__":
    main()
