#!/usr/bin/env python3
"""
The startup checks.

These matter more than their size suggests. JCIAlert.exe is built
`--noconsole`, so a failed start has no stderr: the process ends and the user
sees nothing at all. That is the whole of the 2026-09-17 report - "it doesn't
show on the tray" - because silence was all the app could produce.

Every branch is driven against a real temporary folder rather than a mocked
filesystem, because the interesting cases are all filesystem facts.
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jcidoctor as D                                       # noqa: E402

failures = []


def check(name, cond, detail=None):
    if cond:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name}" + (f"   -> {detail!r}" if detail is not None else ""))


def levels(findings):
    return {f.what: f.level for f in findings}


def main():
    tmp = tempfile.mkdtemp()

    print("\n== the exe copied on its own - the most likely cause ==")
    out = D.check_files(tmp)
    bad = D.fatal(out)
    check("a missing emiten.json is FATAL, not a warning", len(bad) == 1, out)
    check("and the message says what to do about it",
          "whole folder" in bad[0].detail.lower()
          or "installer" in bad[0].detail.lower(), bad[0].detail)
    check("a missing config.json is only a warning - one gets written",
          any(f.level == D.WARN and "config" in f.what for f in out), out)

    with open(os.path.join(tmp, "emiten.json"), "w") as f:
        f.write('{"emiten": {}}')
    check("with the file there, nothing is fatal",
          D.fatal(D.check_files(tmp)) == [])

    empty = tempfile.mkdtemp()
    open(os.path.join(empty, "emiten.json"), "w").close()
    check("a ZERO-BYTE emiten.json is caught too - a half-finished copy is "
          "not a file", D.fatal(D.check_files(empty)) != [])

    print("\n== a folder that cannot be written to ==")
    check("a normal folder passes", D.check_writable(tmp).level == D.OK)
    check("and a path that cannot even be created is FATAL",
          D.check_writable(os.path.join(tmp, "emiten.json", "nope")).level
          == D.FATAL)

    # The bug this project already shipped once, in IDXAlert 2.x: a share that
    # allows create but not unlink is WRITABLE. Requiring the probe to delete
    # cleanly exiled the whole app to LocalAppData.
    class NoUnlink:
        def __enter__(self):
            self.real = os.remove
            os.remove = self._boom
            return self

        def _boom(self, *_a):
            raise PermissionError("unlink is not permitted here")

        def __exit__(self, *_a):
            os.remove = self.real

    with NoUnlink():
        check("create-but-not-delete still counts as writable",
              D.check_writable(tmp).level == D.OK)

    print("\n== a broken config.json ==")
    cfg = os.path.join(tmp, "config.json")
    with open(cfg, "w") as f:
        f.write("{ not json")
    check("invalid JSON is FATAL", D.check_config(cfg).level == D.FATAL)
    check("and the parser's own complaint is carried through, not swallowed",
          D.check_config(cfg).detail != "")
    with open(cfg, "w") as f:
        f.write('{"watchlist": []}')
    check("valid JSON passes", D.check_config(cfg).level == D.OK)
    check("and no config at all is a warning, not a failure",
          D.check_config(os.path.join(tmp, "nope.json")).level == D.WARN)

    print("\n== a second copy already running ==")
    f = D.check_single_instance(True)
    check("it is reported, not silent", f.level == D.FATAL)
    check("and it points at the hidden-icons chevron, which is where it "
          "usually is", "chevron" in f.detail)
    check("one copy is fine", D.check_single_instance(False).level == D.OK)

    print("\n== the report a person reads ==")
    txt = D.report(D.run(tmp, tmp, want_imports=False), tmp, "JCIAlert 9.9")
    check("it names the version", "JCIAlert 9.9" in txt)
    check("and the folder, so 'which copy' is answerable", tmp in txt)
    check("a clean run says so in plain words",
          "No problems found" in D.report(
              D.run(tmp, tmp, want_imports=False), tmp))

    broken = tempfile.mkdtemp()
    rep = D.report(D.run(broken, broken, want_imports=False), broken)
    check("problems come FIRST - they are why it was opened",
          rep.index("FATAL") < (rep.index("Checked and fine")
                                if "Checked and fine" in rep else len(rep)),
          rep[:120])
    check("every fatal finding appears in the text",
          all(x.what in rep for x in D.fatal(D.run(broken, broken,
                                                   want_imports=False))))

    print("\n== saying it where a --noconsole exe can be heard ==")
    where = D.save("hello", "test-note.txt")
    check("the note is written somewhere always writable", bool(where), where)
    if where:
        check("and it contains what was passed",
              open(where, encoding="utf-8").read() == "hello")
        check("under LOCALAPPDATA or the home folder, never the app folder",
              "JCIAlert" in where)
        try:
            os.remove(where)
        except OSError:
            pass
    check("tell() never raises off Windows - it just reports it could not",
          D.tell("t", "m") in (True, False))

    print("\n== the whole run, wired as the tray calls it ==")
    good = tempfile.mkdtemp()
    shutil.copy(os.path.join(tmp, "emiten.json"), good)
    shutil.copy(cfg, good)
    findings = D.run(good, good, already_running=False, want_imports=False)
    check("a correct install is clean", D.fatal(findings) == [],
          [x.what for x in D.fatal(findings)])
    check("the data folder is checked separately when it differs",
          any("data folder" in x.what
              for x in D.run(good, tempfile.mkdtemp(), want_imports=False)))
    check("and not twice when it is the same folder",
          sum("data folder" in x.what for x in findings) == 0)

    print("\n%d checks failed" % len(failures) if failures
          else "\nall checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
