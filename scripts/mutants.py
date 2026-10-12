#!/usr/bin/env python3
"""Gate G4 must be able to fail (docs/design.md section 10).

Copies the repository to a temporary directory, applies one mutation at a time
and requires `scripts/manifest.py` to refuse each. A mutant the gate accepts is
a gate that does not work, and this script exits 1.

    python3 scripts/mutants.py

Mutants: a foreign call; each of the two TLS paths widened (the certificate directory to /etc, the entropy file to /dev); a
ceiling that no longer allows a label the program uses (each of the two paths, SIGHUP, the others); and an embedded report that
is not the compiler's.

What the gate does not judge: a read of another file through a handle narrowed to one path. That was tried as a mutant and the
gate accepted it, because the derived label is the handle's, not the path opened; the gate pins the paths `main` names. Whether
the runtime refuses such an open was not tested here.
The compiler is $CANCHO, or `cancho` on PATH.
"""

import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

FFI_CALL = '''    let libc = narrow(ffi, "libc");
    borrow libc as &lf in {
        pid(lf);
    }
    release(libc);
'''
FFI_DECL = 'extern fn getpid[&f](ffi: &f Ffi("libc")) -> [ffi("libc")] int;\n\nfn pid[&f](libc: &f Ffi("libc")) -> [ffi("libc")] int {\n    return getpid(libc);\n}\n\n'


def edit_main(text, name):
    if name == "ffi":
        text = text.replace("fn main(", FFI_DECL + "fn main(", 1)
        return text.replace("    release(ffi);\n", FFI_CALL, 1)
    raise KeyError(name)


def widen(old, label):
    """The path `old` widened to `label` everywhere a source names it: the narrowing in `main`, and the types and rows in
    `mqtt.tlsfiles`. What a person could do to let the program read more of the disk, and what the ceiling must still refuse."""
    def main_(t):
        return t.replace('"%s"' % old, '"%s"' % label, 1)

    def files(t):
        return t.replace('Fs("%s")' % old, 'Fs("%s")' % label).replace('fs_read("%s")' % old, 'fs_read("%s")' % label)
    return [("src/main.cho", main_), ("src/tlsfiles.cho", files)]


# (name, edits, whether the committed record and embedded report are regenerated first)
def mutants():
    yield "ffi", [("src/main.cho", lambda t: edit_main(t, "ffi"))], True
    yield "certificate directory widened to /etc", widen("/etc/cancho/mqtt", "/etc"), True
    yield "entropy file widened to /dev", widen("/dev/urandom", "/dev"), True
    yield "ceiling lacks io_read", [("ceiling.toml", lambda t: t.replace('"io_read", ', "", 1))], False
    yield "ceiling lacks poll", [("ceiling.toml", lambda t: t.replace('"poll", ', "", 1))], False
    yield "ceiling lacks urandom", [("ceiling.toml", lambda t: t.replace('"fs_read(\\"/dev/urandom\\")", ', "", 1))], False
    yield "ceiling lacks certificate directory", [("ceiling.toml", lambda t: t.replace('"fs_read(\\"/etc/cancho/mqtt\\")", ', "", 1))], False
    yield "ceiling lacks SIGHUP", [("ceiling.toml", lambda t: t.replace('HUP,', "", 1))], False
    yield "stale embedded report", [("generated/mqtt/built.cho", lambda t: t.replace('\\"bounded\\":true', '\\"bounded\\":false', 1))], False


def main():
    failed = []
    for name, edits, regenerate in mutants():
        with tempfile.TemporaryDirectory() as tmp:
            work = pathlib.Path(tmp) / "repo"
            shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns(".git", "build"))
            unchanged = False
            for rel, change in edits:
                target = work / rel
                before = target.read_text()
                after = change(before)
                if after == before:
                    unchanged = True
                target.write_text(after)
            if unchanged:
                print("ERROR %s: the mutation changed nothing" % name)
                failed.append(name)
                continue
            # The source mutants must be refused on their own merits, so the record and the embedded report are regenerated for
            # them; the others are refused by --check against the committed files.
            args = [sys.executable, str(work / "scripts" / "manifest.py")]
            if not regenerate:
                args.append("--check")
            out = subprocess.run(args, capture_output=True, text=True, cwd=work)
            fails = [l for l in out.stdout.splitlines() if l.startswith("FAIL")]
            if out.returncode == 0:
                print("SURVIVED %s: the gate accepted it" % name)
                failed.append(name)
            elif not fails:
                # Refused by something other than the gate (a compile error in the mutant): the mutant proves nothing.
                print("INVALID  %s: %s" % (name, (out.stdout + out.stderr).strip().splitlines()[0]))
                failed.append(name)
            else:
                print("killed   %-24s %s" % (name, fails[0]))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
