"""
probe_sources.py - JCIAlert source feasibility probe.

Answers one question: which Indonesian market-news sources will a plain
stdlib Python client on THIS machine actually fetch, how fast, and how fresh
is what comes back.

Uses THE APP'S OWN client config, imported from jcinet rather than copied:
the header set and the ALPN-pinned TLS context here were a second copy of the
measured set, and a probe that answers "will the app fetch this" with a
different client than the app uses answers the wrong question the moment the
two drift.

Run:  python probe_sources.py
      python probe_sources.py --verbose      # newest 3 headlines each
      python probe_sources.py --only rss     # or: html, unknown

A 200 IS NOT ENOUGH. Two feeds in the first run returned a perfectly valid 200
with a perfectly valid RSS body whose newest item was months or years old -
Okezone's was from July 2016. That is the same class of failure as IDXAlert's
page-two bug: valid response, wrong content, no error anywhere. So this reports
the AGE of the newest item and marks anything past --stale-hours as STALE.

Run it on a WEEKDAY during market hours. On a Saturday every feed is filler and
you cannot judge whether a source is actually carrying emiten news.
"""

import argparse
import http.client
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urljoin
from xml.etree import ElementTree as ET

from jcinet import HEADERS, MAX_REDIRECTS, _inflate, make_context

TIMEOUT = 15

# name, url, kind, status-as-last-measured
# kind: "rss" parses as XML · "html" server-rendered listing page
SOURCES = [
    # ---- confirmed working from Gill's PC, 2026-09-05 --------------------
    ("CNBC Indonesia / market", "https://www.cnbcindonesia.com/market/rss", "rss", "ok"),
    ("CNBC Indonesia / news", "https://www.cnbcindonesia.com/news/rss", "rss", "ok"),
    ("Detik Finance", "https://finance.detik.com/rss", "rss", "ok"),
    ("Katadata", "https://katadata.co.id/rss", "rss", "ok"),
    ("Antara / ekonomi", "https://www.antaranews.com/rss/ekonomi.xml", "rss", "ok"),
    ("Kontan / investasi", "https://investasi.kontan.co.id/rss", "rss", "ok robots?"),
    ("IQPlus / market news", "https://www.iqplus.info/news/market_news/", "html", "ok"),
    ("IQPlus / company news", "https://www.iqplus.info/news/company_news/", "html", "ok slow"),
    ("IQPlus / stock news", "https://www.iqplus.info/news/stock_news/", "html", "ok slow"),
    ("EmitenNews", "https://www.emitennews.com/", "html", "ok"),
    ("Investor.id / market", "https://investor.id/market", "html", "ok"),

    # ---- 200 but the content was DEAD. keep probing, they may revive -----
    ("Okezone / economy", "https://sindikasi.okezone.com/index.php/rss/6/RSS2.0", "rss", "stale 2016"),
    ("Tempo / bisnis", "https://rss.tempo.co/bisnis", "rss", "stale"),

    # ---- refused or wrong path. alternates below --------------------------
    ("Bisnis.com / market", "https://market.bisnis.com/rss", "rss", "403"),
    ("IDN Financials", "https://www.idnfinancials.com/news", "html", "403"),
    ("Pasardana", "https://pasardana.id/news/", "html", "308 -> now followed"),
    ("Kontan (root)", "https://www.kontan.co.id/rss", "rss", "returns html"),

    # ---- path candidates for the two 404s --------------------------------
    ("Liputan6 / rss root", "https://www.liputan6.com/feed/rss", "unknown", "try"),
    ("Liputan6 / bisnis alt", "https://feed.liputan6.com/rss/bisnis", "unknown", "try"),
    ("Liputan6 / saham alt", "https://feed.liputan6.com/rss2/saham", "unknown", "try"),
    ("Kompas / rss root", "https://www.kompas.com/rss", "unknown", "try"),
    ("Kompas / money alt", "https://money.kompas.com/rss/", "unknown", "try"),

    # ---- untested candidates, incl. two from the auto-news scraper --------
    ("IDX Channel", "https://www.idxchannel.com/rss", "unknown", "try"),
    ("Warta Ekonomi", "https://wartaekonomi.co.id/rss", "unknown", "try"),
    ("Kontan / keuangan", "https://keuangan.kontan.co.id/rss", "unknown", "try"),
    ("Bisnis.com / root", "https://www.bisnis.com/rss", "unknown", "try"),
    ("CNBC Indonesia / root", "https://www.cnbcindonesia.com/rss", "unknown", "try"),

    # --- 2026-09-14, Gill asked for Investor Daily and Bisnis Indonesia.
    # Neither can be measured from the Claude container or the desktop VM
    # (both egress-blocked), and bisnis.com already 403'd from Gill's own IP
    # on 2026-09-05 - so every plausible path for both is listed here and ONE
    # run on his PC settles it:  python probe_sources.py --only investor,bisnis
    #                                                    --save-fixtures probe/
    # An RSS hit can be wired straight in; an HTML-only hit needs the fixture,
    # because the parser has to be written against the real markup.
    ("Investor.id / rss", "https://investor.id/rss", "unknown", "try"),
    ("Investor.id / feed", "https://investor.id/feed", "unknown", "try"),
    ("Investor.id / market rss", "https://investor.id/market/rss", "unknown", "try"),
    ("Investor.id / index.xml", "https://investor.id/index.xml", "unknown", "try"),
    ("Investor.id / market (html)", "https://investor.id/market", "html", "worked 09-05"),
    ("Investor.id / root (html)", "https://investor.id/", "html", "try"),
    ("Bisnis / market rss", "https://market.bisnis.com/rss", "unknown", "403 on 09-05"),
    ("Bisnis / root rss", "https://www.bisnis.com/rss", "unknown", "403 on 09-05"),
    ("Bisnis / market html", "https://market.bisnis.com/", "html", "try"),
    ("Bisnis / feed alt", "https://www.bisnis.com/index/rss", "unknown", "try"),
    ("Bisnis / rss index", "https://www.bisnis.com/rss/index", "unknown", "try"),
    ("Bisnis / market index", "https://market.bisnis.com/index", "html", "try"),

    # --- Kompas, 2026-09-14. The three paths probed on 09-05 all 404'd, but
    # they were the OLD scheme - Kompas moved its feeds to an rss. subdomain.
    # These are candidates, NOT measurements; one run on Gill's PC decides.
    ("Kompas / rss api", "https://rss.kompas.com/api/feed", "unknown", "try"),
    ("Kompas / rss money", "https://rss.kompas.com/api/feed/money", "unknown", "try"),
    ("Kompas / rss root", "https://rss.kompas.com/", "unknown", "try"),
    ("Kompas / money feed", "https://money.kompas.com/rss/feed", "unknown", "try"),
    ("Kompas / indeks rss", "https://indeks.kompas.com/rss", "unknown", "try"),
    ("Kompas / money html", "https://money.kompas.com/", "html", "try"),
]


def fetch(url, depth=0, extra=None):
    """(status, content_type, body, ms, final_url). Follows redirects.

    Deliberately NOT jcinet.Fetcher.get: that raises on 403/429/503 and on any
    non-200, because the app has nothing useful to do with them. Reporting
    exactly those statuses is this script's entire job, so it keeps its own
    single-shot request - but over jcinet's headers and TLS context, not a
    second copy of them.
    """
    p = urlsplit(url)
    t0 = time.time()
    if p.scheme == "https":
        conn = http.client.HTTPSConnection(p.netloc, timeout=TIMEOUT,
                                           context=make_context())
    else:
        conn = http.client.HTTPConnection(p.netloc, timeout=TIMEOUT)
    try:
        path = (p.path or "/") + (("?" + p.query) if p.query else "")
        conn.request("GET", path,
                     headers={**HEADERS, **(extra or {}), "Host": p.netloc,
                              "Connection": "close"})
        r = conn.getresponse()
        body = r.read()
        # 301/302/307/308 - several of these sites redirect http->https or
        # bare-path -> trailing-slash, and not following made them look dead.
        if r.status in (301, 302, 303, 307, 308) and depth < MAX_REDIRECTS:
            loc = r.getheader("Location")
            if loc:
                return fetch(urljoin(url, loc), depth + 1, extra)
        return (r.status, (r.getheader("Content-Type") or ""),
                _inflate(body, r.getheader("Content-Encoding")),
                int((time.time() - t0) * 1000), url)
    finally:
        conn.close()


def fetch_twice(url):
    """A 403 is retried ONCE as if a browser already on the site had asked.

    The header set is already the full measured Chrome one (see
    feedback-headers), so a 403 is not explained by a trimmed header list. What
    it can still be explained by is the request looking like it arrived from
    nowhere: `Sec-Fetch-Site: none` with no Referer is a direct address-bar
    hit, and some WAFs price that differently from a same-site fetch. This is
    the only other lever worth pulling before calling a source blocked, and it
    is pulled here rather than in jcinet so the app's behaviour stays the thing
    that was measured.
    """
    got = fetch(url)
    if got[0] != 403:
        return got, ""
    p = urlsplit(url)
    same_site = {
        "Referer": f"{p.scheme}://{p.netloc}/",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
    }
    retry = fetch(url, extra=same_site)
    return retry, (" (needed a Referer)" if retry[0] == 200 else "")


ATOM = "{http://www.w3.org/2005/Atom}"


def parse_rss(body):
    """(count, [(title, pubdate_text, datetime|None), ...])."""
    root = ET.fromstring(body)
    items = root.findall(".//item") or root.findall(f".//{ATOM}entry")
    out = []
    for it in items[:3]:
        t = (it.findtext("title") or it.findtext(ATOM + "title") or "?").strip()
        d = (it.findtext("pubDate") or it.findtext(ATOM + "updated")
             or it.findtext("{http://purl.org/dc/elements/1.1/}date") or "").strip()
        when = None
        if d:
            try:
                when = parsedate_to_datetime(d)
            except Exception:
                try:
                    when = datetime.fromisoformat(d.replace("Z", "+00:00"))
                except Exception:
                    when = None
            if when is not None and when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
        out.append((t[:76], d, when))
    return len(items), out


def age_str(when, now):
    if when is None:
        return "  ?  "
    h = (now - when).total_seconds() / 3600.0
    if h < 0:
        return " future"
    if h < 48:
        return f"{h:5.1f}h"
    return f"{h/24:5.1f}d"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--only", default="",
                    help="a kind (rss | html | unknown) or a comma-separated "
                         "list of name fragments, e.g. --only investor,bisnis")
    ap.add_argument("--stale-hours", type=float, default=48.0)
    ap.add_argument("--save-fixtures", metavar="DIR", default="",
                    help="write each source's RAW bytes to DIR plus a manifest, "
                         "so parsers can be written and tested offline against "
                         "what these sites actually serve")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    fixdir = args.save_fixtures
    manifest = []
    if fixdir:
        os.makedirs(fixdir, exist_ok=True)
    # --only takes a kind OR name fragments. Probing one outlet is the common
    # case - "does investor.id have a feed" - and having to run all thirty to
    # find out is why the flag grew the second meaning.
    want = [w.strip().lower() for w in (args.only or "").split(",") if w.strip()]
    if not want:
        rows = list(SOURCES)
    elif len(want) == 1 and want[0] in ("rss", "html", "unknown"):
        rows = [s for s in SOURCES if s[2] == want[0]]
    else:
        rows = [s for s in SOURCES
                if any(w in s[0].lower() or w in s[1].lower() for w in want)]
    if not rows:
        print(f"nothing matches --only {args.only!r}")
        return 1

    print(f"{'source':<26} {'code':>4} {'ms':>6} {'items':>5} {'age':>7}  verdict")
    print("-" * 100)
    tally = {}
    for name, url, kind, _prev in rows:
        try:
            (code, ctype, body, ms, final), note = fetch_twice(url)
        except Exception as e:
            print(f"{name:<26} {'ERR':>4} {'':>6} {'':>5} {'':>7}  "
                  f"{type(e).__name__}: {str(e)[:40]}")
            tally["error"] = tally.get("error", 0) + 1
            continue

        n, age, verdict, head = "", "", "", []
        if code != 200:
            verdict = f"BLOCKED / {code}"
        else:
            looks_xml = body.lstrip()[:200].startswith(b"<?xml") or b"<rss" in body[:400]
            if kind == "rss" or (kind == "unknown" and looks_xml):
                try:
                    cnt, head = parse_rss(body)
                    n = str(cnt)
                    newest = head[0][2] if head else None
                    age = age_str(newest, now)
                    if cnt == 0:
                        verdict = "EMPTY FEED"
                    elif newest is None:
                        verdict = "ok (no parseable date)"
                    elif (now - newest).total_seconds() / 3600.0 > args.stale_hours:
                        verdict = "STALE - do not use"
                    else:
                        verdict = "OK"
                except ET.ParseError:
                    verdict = f"NOT XML ({ctype.split(';')[0]}, {len(body)//1024}kb)"
            else:
                n = str(body.count(b"<a "))
                verdict = f"OK html {len(body)//1024}kb"
        verdict += note

        key = ("OK" if verdict.startswith("OK") else
               "stale" if verdict.startswith("STALE") else "unusable")
        tally[key] = tally.get(key, 0) + 1
        if fixdir and code == 200 and body:
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            ext = "xml" if (kind == "rss" or b"<rss" in body[:400]) else "html"
            fn = f"{slug}.{ext}"
            with open(os.path.join(fixdir, fn), "wb") as fh:
                fh.write(body)
            manifest.append({"name": name, "file": fn, "url": url,
                             "final_url": final, "kind": kind, "status": code,
                             "content_type": ctype, "bytes": len(body),
                             "fetched_at": now.isoformat(timespec="seconds"),
                             "items_or_links": n, "verdict": verdict})
        print(f"{name:<26} {code:>4} {ms:>6} {n:>5} {age:>7}  {verdict}")
        if args.verbose and head:
            for t, _d, w in head:
                print(f"{'':<26} · [{age_str(w, now).strip():>6}] {t}")

    print("-" * 100)
    print(" · ".join(f"{k} {v}" for k, v in sorted(tally.items())))
    if fixdir:
        with open(os.path.join(fixdir, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"captured": now.isoformat(timespec="seconds"),
                       "sources": manifest}, fh, ensure_ascii=False, indent=1)
        print(f"\nwrote {len(manifest)} fixtures + manifest.json to {fixdir}/")
        print("These are a point-in-time snapshot of what each site serves.")
        print("Parsers get written and regression-tested against them offline.")
    print("\nA 200 with a STALE newest item is the dangerous result: valid")
    print("response, wrong content, nothing errors. Same shape as the page-two")
    print("bug. Re-run on a weekday during market hours before committing.")


if __name__ == "__main__":
    sys.exit(main())
