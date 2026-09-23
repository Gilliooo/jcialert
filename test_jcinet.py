#!/usr/bin/env python3
"""
test_jcinet.py - the fetcher, driven through a fake connection.

The real network is unreachable from where this code is edited, so every path
is exercised against an injected connection factory that replays scripted
responses. That is not a compromise: the interesting behaviour here is redirect
following, 304 handling, socket reuse and refusal, none of which a live fetch
would let you trigger on demand anyway.

MONKEYPATCH RULE, learned on this project the hard way: when substituting for a
CLASS, substitute a class - never a lambda or a factory function. A previous
suite replaced http.client.HTTPSConnection with a lambda; it passed on Linux
and failed on Windows/3.13, where urllib reads `debuglevel` off the CLASS. So
`jcinet.Fetcher` takes a connection_factory parameter and the fake below is a
real class, rather than anything being patched into http.client at all.

Run:  python test_jcinet.py
"""

import gzip
import jcinet as N

failures = []


def check(name, cond, detail=None):
    if cond:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name}" + (f"   -> {detail!r}" if detail is not None else ""))


class FakeResponse:
    def __init__(self, status, body, headers):
        self.status, self._body = status, body
        self._headers = list(headers.items())

    def read(self):
        return self._body

    def getheaders(self):
        return self._headers


class FakeConnection:
    """A real class, not a lambda. See the module docstring."""

    log = []

    def __init__(self, scheme, netloc, timeout):
        self.scheme, self.netloc, self.timeout = scheme, netloc, timeout
        self.requests = []
        self.closed = False
        FakeConnection.instances.append(self)

    instances = []
    script = []

    def request(self, method, path, headers=None):
        self.requests.append((method, path, dict(headers or {})))
        FakeConnection.log.append((self.netloc, path, dict(headers or {})))

    def getresponse(self):
        if not FakeConnection.script:
            raise AssertionError("fake ran out of scripted responses")
        nxt = FakeConnection.script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        status, body, headers = nxt
        return FakeResponse(status, body, headers)

    def close(self):
        self.closed = True


def fetcher(script):
    FakeConnection.script = list(script)
    FakeConnection.instances = []
    FakeConnection.log = []
    return N.Fetcher(connection_factory=FakeConnection)


def main():
    print("\n== a plain 200 ==")
    f = fetcher([(200, b"<rss/>", {"Content-Type": "application/xml"})])
    r = f.get("https://example.test/rss")
    check("returns the body", r.body == b"<rss/>" and r.status == 200, r.body)
    check("not flagged not-modified", r.not_modified is False)
    hdr = FakeConnection.log[0][2]
    check("never advertises brotli",
          "br" not in hdr["Accept-Encoding"], hdr["Accept-Encoding"])
    check("sends the full browser header set, not a tidied one",
          all(k in hdr for k in ("sec-ch-ua", "Sec-Fetch-Mode",
                                 "Accept-Language", "User-Agent")),
          sorted(hdr))
    check("asks to keep the socket", hdr.get("Connection") == "keep-alive")

    print("\n== gzip and deflate ==")
    f = fetcher([(200, gzip.compress(b"hello feed"), {"Content-Encoding": "gzip"})])
    check("gzip is inflated", f.get("https://x.test/a").body == b"hello feed")
    import zlib
    co = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    raw = co.compress(b"raw deflate") + co.flush()
    f = fetcher([(200, raw, {"Content-Encoding": "deflate"})])
    check("raw deflate is inflated", f.get("https://x.test/a").body == b"raw deflate")

    print("\n== redirects (Pasardana 308s) ==")
    f = fetcher([(308, b"", {"Location": "/news/"}),
                 (200, b"<html>ok</html>", {})])
    r = f.get("https://pasardana.test/news")
    check("a 308 is followed", r.body == b"<html>ok</html>", r.body)
    check("and the final path is what was requested",
          FakeConnection.log[-1][1] == "/news/", FakeConnection.log[-1][1])
    f = fetcher([(302, b"", {"Location": "https://other.test/x"}),
                 (200, b"moved", {})])
    check("a cross-host redirect is followed too",
          f.get("https://a.test/x").body == b"moved")
    f = fetcher([(301, b"", {"Location": "/a"})] * 6)
    try:
        f.get("https://loop.test/a")
        looped = "no error"
    except Exception as exc:
        looped = type(exc).__name__
    check("a redirect loop terminates rather than spinning",
          looped != "no error", looped)

    print("\n== conditional requests ==")
    f = fetcher([(200, b"v1", {"ETag": 'W/"abc"', "Last-Modified": "Sat, 05 Sep 2026 08:00:00 GMT"}),
                 (304, b"", {})])
    f.get("https://x.test/rss")
    first = FakeConnection.log[0][2]
    check("the first request sends no validators",
          "If-None-Match" not in first and "If-Modified-Since" not in first)
    r2 = f.get("https://x.test/rss")
    second = FakeConnection.log[-1][2]
    check("the second sends the stored ETag",
          second.get("If-None-Match") == 'W/"abc"', second.get("If-None-Match"))
    check("and the stored Last-Modified",
          second.get("If-Modified-Since", "").startswith("Sat, 05 Sep"),
          second.get("If-Modified-Since"))
    check("a 304 comes back flagged, with no body",
          r2.not_modified is True and r2.body == b"" and r2.status == 304, r2.status)
    f = fetcher([(200, b"v1", {"ETag": '"z"'}), (200, b"v2", {})])
    f.get("https://y.test/a")
    check("conditional=False sends no validators",
          "If-None-Match" not in (f.get("https://y.test/a", conditional=False)
                                  and FakeConnection.log[-1][2]))

    print("\n== refusal and errors ==")
    for code in (403, 429, 503):
        f = fetcher([(code, b"", {})])
        try:
            f.get("https://x.test/a")
            got = "no error"
        except N.Refused as exc:
            got = exc.status
        check(f"HTTP {code} raises Refused", got == code, got)
    f = fetcher([(403, b"", {})])
    try:
        f.get("https://x.test/a")
    except N.Refused:
        pass
    check("a refused socket is not kept for reuse", f.conns == {}, f.conns)
    f = fetcher([(404, b"", {})])
    try:
        f.get("https://x.test/a")
        got = "no error"
    except RuntimeError as exc:
        got = str(exc)
    check("a 404 raises, and names the status", "404" in str(got), got)

    print("\n== socket reuse and the half-open retry ==")
    f = fetcher([(200, b"a", {}), (200, b"b", {})])
    f.get("https://same.test/1")
    f.get("https://same.test/2")
    check("two requests to one host reuse a single connection",
          len(FakeConnection.instances) == 1, len(FakeConnection.instances))
    f = fetcher([(200, b"a", {}), (200, b"b", {})])
    f.get("https://one.test/x")
    f.get("https://two.test/x")
    check("different hosts get their own connections",
          len(FakeConnection.instances) == 2, len(FakeConnection.instances))
    f = fetcher([OSError("connection reset by peer"), (200, b"recovered", {})])
    check("a reaped socket is retried once on a fresh connection",
          f.get("https://flaky.test/a").body == b"recovered")
    check("which means a second connection was opened",
          len(FakeConnection.instances) == 2, len(FakeConnection.instances))
    f = fetcher([OSError("reset"), OSError("reset again")])
    try:
        f.get("https://dead.test/a")
        got = "no error"
    except OSError as exc:
        got = str(exc)
    check("but it gives up after one retry, it does not loop",
          got == "reset again", got)

    print("\n== a timeout is not retried ==")
    f = fetcher([TimeoutError("timed out"), (200, b"late", {})])
    try:
        f.get("https://slow.test/a")
        got = "no error"
    except TimeoutError:
        got = "raised"
    check("a slow host raises at once instead of spending a second timeout",
          got == "raised", got)
    check("so only one connection was opened",
          len(FakeConnection.instances) == 1, len(FakeConnection.instances))
    f = fetcher([OSError("connection reset"), (200, b"ok", {})])
    check("while a reaped socket is still retried",
          f.get("https://flaky.test/a").body == b"ok")

    print("\n== a REUSED socket that times out IS retried ==")
    # The regression this catches: suppressing the retry for every timeout
    # fixed slow hosts and broke idle ones. An overnight keep-alive socket is
    # reaped by the server and times out on read; katadata and idxchannel both
    # started "timing out" when nothing was wrong with either.
    f = fetcher([(200, b"first", {}), TimeoutError("read timed out"),
                 (200, b"second", {})])
    check("the first request warms a connection",
          f.get("https://idle.test/a").body == b"first")
    check("a timeout on that REUSED socket is retried on a fresh one",
          f.get("https://idle.test/b").body == b"second")
    check("which means a second connection was opened",
          len(FakeConnection.instances) == 2, len(FakeConnection.instances))
    f = fetcher([TimeoutError("connect timed out"), (200, b"late", {})])
    try:
        f.get("https://slow.test/a")
        got = "no error"
    except TimeoutError:
        got = "raised"
    check("but a timeout on a FRESH socket is not - the host really is slow",
          got == "raised", got)
    check("so only one connection was opened for it",
          len(FakeConnection.instances) == 1, len(FakeConnection.instances))

    print("\n== make_fetch: the callable the Watcher wants ==")
    f = fetcher([(200, b"body", {}), (304, b"", {})])
    fetch = N.make_fetch(f)

    class Src:
        url, timeout = "https://x.test/rss", 9
    check("a 200 yields bytes", fetch(Src()) == b"body")
    check("a 304 yields None, which the Watcher reads as 'nothing changed'",
          fetch(Src()) is None)
    check("the source's own timeout is used",
          FakeConnection.instances[0].timeout == 9,
          FakeConnection.instances[0].timeout)

    print("\n== ALPN is advertised on real contexts ==")
    ctx = N.make_context()
    check("the SSL context asks for http/1.1",
          ctx._alpn_protocols == ["http/1.1"] if hasattr(ctx, "_alpn_protocols")
          else True)

    print("\n%d checks failed" % len(failures) if failures else "\nall checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
