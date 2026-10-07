#!/usr/bin/env python3
"""Gate G4 must be able to fail (docs/design.md section 10).

Copies the repository to a temporary directory, applies one mutation at a time
and requires `scripts/manifest.py` to refuse each. A mutant the gate accepts is
a gate that does not work, and this script exits 1.

    python3 scripts/mutants.py

Mutants: a file read, a foreign call, a ceiling that no longer allows a label
the program uses, and an embedded report that is not the compiler's.
The compiler is $CANCHO, or `cancho` on PATH.
"""

import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

FILE_READ = '''    borrow fs as &fz in {
        match open_read(fz, "/etc/hostname") {
            Opened::Failed(reason) => {
            }
            Opened::Ok(opened) => {
                var file = opened;
                file_close(file);
            }
        }
    }
    release(fs);
'''

FFI_CALL = '''    let libc = narrow(ffi, "libc");
    borrow libc as &lf in {
        pid(lf);
    }
    release(libc);
'''
FFI_DECL = 'extern fn getpid[&f](ffi: &f Ffi("libc")) -> [ffi("libc")] int;\n\nfn pid[&f](libc: &f Ffi("libc")) -> [ffi("libc")] int {\n    return getpid(libc);\n}\n\n'


def edit_main(text, name):
    if name == "file_read":
        return text.replace("    release(fs);\n", FILE_READ, 1)
    if name == "ffi":
        text = text.replace("fn main(", FFI_DECL + "fn main(", 1)
        return text.replace("    release(ffi);\n", FFI_CALL, 1)
    raise KeyError(name)


def mutants():
    yield "file_read", "src/main.cho", lambda t: edit_main(t, "file_read")
    yield "ffi", "src/main.cho", lambda t: edit_main(t, "ffi")
    yield "ceiling lacks poll", "ceiling.toml", lambda t: t.replace('"poll", ', "", 1)
    yield "stale embedded report", "generated/mqtt/built.cho", lambda t: t.replace('\\"bounded\\":true', '\\"bounded\\":false', 1)


def main():
    failed = []
    for name, rel, change in mutants():
        with tempfile.TemporaryDirectory() as tmp:
            work = pathlib.Path(tmp) / "repo"
            shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns(".git", "build"))
            target = work / rel
            before = target.read_text()
            after = change(before)
            if after == before:
                print("ERROR %s: the mutation changed nothing" % name)
                failed.append(name)
                continue
            target.write_text(after)
            # file_read and ffi must be refused on their own merits, so the
            # record and the embedded report are regenerated for them; the last
            # two are refused by --check against the committed files.
            regenerate = name in ("file_read", "ffi")
            args = [sys.executable, str(work / "scripts" / "manifest.py")]
            if not regenerate:
                args.append("--check")
            out = subprocess.run(args, capture_output=True, text=True, cwd=work)
            fails = [l for l in out.stdout.splitlines() if l.startswith("FAIL")]
            if out.returncode == 0:
                print("SURVIVED %s: the gate accepted it" % name)
                failed.append(name)
            elif not fails:
                # Refused by something other than the gate (a compile error in
                # the mutant): the mutant proves nothing.
                print("INVALID  %s: %s" % (name, (out.stdout + out.stderr).strip().splitlines()[0]))
                failed.append(name)
            else:
                print("killed   %-24s %s" % (name, fails[0]))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
