#!/usr/bin/env python3
"""
The portable zip, checked against the script that builds it.

make_portable.bat cannot be run here - it needs a PyInstaller build of the
exe first - so it is checked the way every other unrunnable surface in this
project is: statically, against its own source text.
"""

import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))

failures = []


def check(name, cond, detail=None):
    if cond:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name}" + (f"   -> {detail!r}" if detail is not None else ""))


def main():
    print("\n== the no-tooling fallback needs no script ==")
    # The portable zip used to ship SETUP.bat/REMOVE.bat, installing into
    # LOCALAPPDATA. Removed: config.json already lives beside the exe, so
    # there was nothing an install step bought except a Start Menu shortcut.
    # Now the zip is just run-JCIAlert.exe-from-wherever-you-put-it.
    port = os.path.join(HERE, "installer", "portable")
    check("SETUP.bat is gone - the portable zip installs nothing",
          not os.path.exists(os.path.join(port, "SETUP.bat")))
    check("REMOVE.bat is gone with it",
          not os.path.exists(os.path.join(port, "REMOVE.bat")))
    readme = open(os.path.join(port, "READ-ME-FIRST.txt"),
                  encoding="utf-8").read()
    check("READ-ME-FIRST.txt tells people to just run the exe",
          "SETUP.bat" not in readme and "JCIAlert.exe" in readme)
    check("the superseded install.bat/uninstall.bat pair is gone",
          not os.path.exists(os.path.join(HERE, "installer", "install.bat")))

    print("\n== the portable folder is BUILT, not filled by hand ==")
    # The 1.0.1 zip shipped with the developer's own seen.json, news.csv,
    # opened.json and logs\ in it, because the folder was assembled by hand
    # and nothing ever emptied it. A colleague unzipping that would have
    # started mid-history on someone else's story clusters.
    mp = os.path.join(HERE, "make_portable.bat")
    check("make_portable.bat exists", os.path.exists(mp), mp)
    if os.path.exists(mp):
        m = open(mp, encoding="utf-8").read()
        check("it empties the folder before filling it, so nothing survives "
              "from a previous build",
              re.search(r"rd /s /q ", m) is not None
              and m.index("rd /s /q ") < m.index("copy /y"))
        for never in ("seen.json", "news.csv", "opened.json", "alerts.csv"):
            check(f"{never} is never copied into the zip",
                  never not in m or ("copy" not in m[m.index(never) - 60:
                                                     m.index(never)]), never)
        check("the version comes from jci.VERSION, not a literal",
              "jci;print(jci.VERSION)" in m
              and not re.search(r'ZIP=.*\d+\.\d+', m),
              [ln for ln in m.splitlines() if "ZIP=" in ln])
        check("and the ticker table goes in, since the exe cannot start "
              "without it", "emiten.json" in m)

    print("\n%d checks failed" % len(failures) if failures
          else "\nall checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
