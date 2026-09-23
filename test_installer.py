#!/usr/bin/env python3
"""
The installer, checked against the code it installs.

An .iss cannot be run here - or anywhere without Inno Setup - so it is checked
the way every other unrunnable surface in this project is: statically, against
the thing it has to agree with. The failures worth catching are all drift.

- The version in the .iss and jci.VERSION are two copies of one fact.
- AppMutex and jcitray's mutex name are two copies of one fact. If they drift,
  Inno stops detecting the running app and the install fails halfway with a
  file-in-use error.
- The Run key name in the uninstaller and jcistartup.NAME are two copies of
  one fact. If they drift, uninstalling leaves a login entry pointing at a
  deleted exe.
- Every file the .iss ships has to exist.
- config.json and seen.json must never be replaced by an upgrade.

Each of those is a copy of something that lives in Python, and a copy is a
thing that goes stale. See feedback-contracts: read the other side, do not
assume it.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jci                                                  # noqa: E402
import jcistartup                                           # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ISS = os.path.join(HERE, "installer", "JCIAlert.iss")

failures = []


def check(name, cond, detail=None):
    if cond:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name}" + (f"   -> {detail!r}" if detail is not None else ""))


def defines(text):
    return dict(re.findall(r'#define\s+(\w+)\s+"([^"]*)"', text))


def expand(value, d):
    """Resolve {#Define} references. The .iss uses them so one fact lives in
    one place; a test that reads the literal text sees `{#ExeName}` and
    reports a file that does not exist - which is the test being wrong about
    the file, not the file being wrong."""
    for _ in range(4):
        new = re.sub(r"\{#(\w+)\}", lambda m: d.get(m.group(1), m.group(0)),
                     value)
        if new == value:
            break
        value = new
    return value


def section(text, name):
    m = re.search(r"^\[%s\]\s*$(.*?)(?=^\[|\Z)" % name, text,
                  re.S | re.M | re.I)
    return m.group(1) if m else ""


def main():
    if not os.path.exists(ISS):
        print(f"  FAIL installer/JCIAlert.iss is missing")
        return 1
    text = open(ISS, encoding="utf-8").read()
    d = defines(text)

    print("\n== the .iss and the code agree on the facts they share ==")
    check("the installer version matches jci.VERSION",
          d.get("AppVersion") == jci.VERSION,
          (d.get("AppVersion"), jci.VERSION))

    tray = open(os.path.join(HERE, "jcitray.py"), encoding="utf-8").read()
    mutex = re.search(r'CreateMutexW\(None,\s*False,\s*"([^"]+)"', tray)
    want = mutex.group(1).replace("\\\\", "\\") if mutex else None
    check("AppMutex is the mutex jcitray actually creates",
          want and d.get("AppMutexName") == want,
          (d.get("AppMutexName"), want))
    check("and AppMutex is wired up, not just defined",
          re.search(r"^AppMutex=", text, re.M) is not None)

    code = section(text, "Code")
    check("the uninstaller deletes the Run value jcistartup writes",
          f"'{jcistartup.NAME}'" in code, jcistartup.NAME)
    check("from the Run key jcistartup uses",
          jcistartup.RUN_KEY.replace("\\", "\\") in code
          or "CurrentVersion\\Run" in code)

    print("\n== every file it ships exists ==")
    # dist\JCIAlert.exe is the ONE exception, and it has to be: this suite runs
    # inside build.bat, BEFORE PyInstaller has built anything. Asserting it
    # exists here made a green build impossible from a clean tree - and it
    # passed the first time only because a stale exe from a previous build
    # happened to be lying around, which is how an ordering bug hides.
    #
    # Whether the exe was built is the BUILD's business, and make_installer.bat
    # checks it explicitly before it compiles anything. This suite's business
    # is whether the .iss and the code agree.
    files = section(text, "Files")
    sources = [expand(x, d) for x in re.findall(r'Source:\s*"([^"]+)"', files)]
    check("the [Files] section is not empty", len(sources) >= 4, sources)
    built = f"dist\\{d.get('ExeName', '?')}"
    for src in sources:
        rel = src.replace("..\\", "").replace("\\", os.sep)
        if src.endswith(built):
            where = os.path.join(HERE, rel)
            print(f"  ok   {rel} is named"
                  + ("" if os.path.exists(where)
                     else "   (not built yet - build.bat makes it)"))
            continue
        check(f"{rel} is there", os.path.exists(os.path.join(HERE, rel)), rel)
    check("it ships the exe",
          any(s.endswith(built) for s in sources), sources)
    check("and emiten.json, which is NOT bundled into the exe",
          any("emiten.json" in s for s in sources), sources)
    check("make_installer.bat is what refuses to compile without the exe",
          "if not exist" in open(os.path.join(HERE, "make_installer.bat"),
                                 encoding="utf-8").read()
          and "JCIAlert.exe" in open(os.path.join(HERE, "make_installer.bat"),
                                     encoding="utf-8").read())

    print("\n== an upgrade must not eat the user's settings or history ==")
    for line in files.splitlines():
        if "config.json" in line and "aliases" not in line:
            check("config.json is onlyifdoesntexist",
                  "onlyifdoesntexist" in line, line.strip())
        if "aliases_manual.json" in line:
            check("aliases_manual.json is onlyifdoesntexist",
                  "onlyifdoesntexist" in line, line.strip())
    for never in ("seen.json", "news.csv", "opened.json"):
        check(f"{never} is not shipped at all - it is the user's",
              never not in files, never)

    print("\n== it installs where the app can actually write ==")
    # config.json lives beside the exe, so a Program Files install produces an
    # app that runs and cannot save a setting.
    check("privileges are 'lowest', so {autopf} is under LocalAppData",
          re.search(r"^PrivilegesRequired=lowest", text, re.M) is not None)
    check("and the wizard refuses a folder it cannot write to",
          "NextButtonClick" in code and "wpSelectDir" in code)
    check("no [Registry] section writes autostart - the app owns that setting",
          not section(text, "Registry").strip(),
          section(text, "Registry")[:80])

    print("\n== the no-tooling fallback agrees with all of it ==")
    # SETUP.bat / REMOVE.bat ship inside the portable zip, which is the route
    # that works on a machine with no Inno Setup. They live in
    # installer\portable\ - SOURCE - and make_portable.bat copies them into
    # the assembled folder. installer\install.bat and uninstall.bat were an
    # earlier pair doing the same job from the build tree; they were deleted
    # on 2026-09-23 rather than kept in step, since two installers that
    # disagree is worse than one.
    port = os.path.join(HERE, "installer", "portable")
    bat = os.path.join(port, "SETUP.bat")
    if os.path.exists(bat):
        b = open(bat, encoding="utf-8").read()
        check("SETUP.bat also installs per-user, not to Program Files",
              "LOCALAPPDATA" in b and "ProgramFiles" not in b)
        check("it refuses to overwrite a running exe",
              "tasklist" in b.lower())
        check("it copies emiten.json, not just the exe",
              "emiten.json" in b)
        check("and it does not clobber an existing config.json",
              re.search(r"if not exist .*config\.json", b) is not None)
        un = open(os.path.join(port, "REMOVE.bat"), encoding="utf-8").read()
        check("REMOVE.bat removes the Run value",
              jcistartup.NAME in un and "reg delete" in un.lower())
        check("and keeps history unless asked to purge",
              "/purge" in un)
    else:
        check("installer/portable carries SETUP.bat", False, bat)
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
