#!/usr/bin/env python3
"""
jcinet - fetching, for news sites rather than for IDX.

WHY THIS IS NOT idx3net
-----------------------
idx3net's clients are bound to `HOST = www.idx.co.id` - `UrllibClient.get()`
literally builds "https://" + HOST + path. They cannot fetch anything else, so
JCIAlert needs its own fetcher. What carries over is not the code, it is the
MEASURED CONSTRAINTS, and they are copied deliberately rather than re-derived:

  · HTTP/1.1 only. http.client is 1.1 by construction, which is convenient.
  · ALPN must be advertised. A custom SSLContext that does not call
    set_alpn_protocols(["http/1.1"]) sends no ALPN at all - a loud non-browser
    fingerprint - and gets a 403. This cost a round trip on build_aliases.py.
  · NEVER "br" in Accept-Encoding. The stdlib cannot inflate brotli; you get a
    200 with an undecodable body that reads like a parser bug.
  · The full browser header set, not a tidied one.

News sites are far softer than idx.co.id - all 17 working sources answered a
plain stdlib client - but the config costs nothing and being wrong costs a
round trip to a machine that is not here.

REDIRECTS AND CONDITIONAL REQUESTS
----------------------------------
Redirects are followed: Pasardana 308s, and not following made it look dead in
the first probe.

Every response's ETag / Last-Modified is remembered and sent back as
If-None-Match / If-Modified-Since. At 60s across ten sources that is ~14,400
requests a day; the ones that answer 304 cost a few hundred bytes instead of
150KB. A 304 is NOT an error and NOT an empty feed - it means "nothing changed",
so `get()` returns a Response with `not_modified` set and the caller skips the
source for that tick without touching its health or its freshness state.
"""

import gzip
import http.client
import io
import ssl
import time
import zlib
from urllib.parse import urlsplit, urljoin

MAX_REDIRECTS = 4

# Copied from the measured set. Do not tidy - see the module docstring.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": ("application/rss+xml, application/xml, text/xml, "
               "text/html;q=0.9, */*;q=0.8"),
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate",
    "sec-ch-ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}


class Refused(Exception):
    """The server answered, and the answer was no. Cool this source down."""

    def __init__(self, status, url):
        super().__init__(f"HTTP {status} for {url}")
        self.status, self.url = status, url


class Response:
    __slots__ = ("status", "body", "headers", "ms", "url", "not_modified")

    def __init__(self, status, body, headers, ms, url, not_modified=False):
        self.status, self.body, self.headers = status, body, headers
        self.ms, self.url, self.not_modified = ms, url, not_modified


def _inflate(raw, encoding):
    enc = (encoding or "").lower()
    if "gzip" in enc:
        return gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    if "deflate" in enc:
        try:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
        except zlib.error:
            return zlib.decompress(raw)
    return raw


def make_context():
    ctx = ssl.create_default_context()
    ctx.set_alpn_protocols(["http/1.1"])     # omit and you get a 403
    return ctx


class Fetcher:
    """Keep-alive connections, one per host, plus conditional-request state.

    Keep-alive matters less for latency than it does for connection count -
    TLS 1.3 resumption absorbs most of the handshake - but connection count is
    what rate limiting keys on, and this polls the same ten hosts all day.
    """

    def __init__(self, timeout=15, log=None, connection_factory=None):
        self.timeout = timeout
        self.log = log or (lambda *a: None)
        self.conns = {}
        self.validators = {}                  # url -> (etag, last_modified)
        self._factory = connection_factory     # tests inject a fake here

    def close(self):
        for c in self.conns.values():
            try:
                c.close()
            except Exception:
                pass
        self.conns.clear()

    def _connect(self, scheme, netloc, timeout):
        if self._factory:
            return self._factory(scheme, netloc, timeout)
        if scheme == "https":
            return http.client.HTTPSConnection(netloc, timeout=timeout,
                                               context=make_context())
        return http.client.HTTPConnection(netloc, timeout=timeout)

    def _conn(self, scheme, netloc, timeout, fresh=False):
        key = (scheme, netloc)
        if fresh or key not in self.conns:
            old = self.conns.pop(key, None)
            if old is not None:
                try:
                    old.close()
                except Exception:
                    pass
            self.conns[key] = self._connect(scheme, netloc, timeout)
        return self.conns[key]

    def get(self, url, timeout=None, conditional=True, _depth=0):
        timeout = timeout or self.timeout
        p = urlsplit(url)
        path = (p.path or "/") + (("?" + p.query) if p.query else "")
        headers = dict(HEADERS, Host=p.netloc, Connection="keep-alive")
        if conditional:
            etag, lastmod = self.validators.get(url, (None, None))
            if etag:
                headers["If-None-Match"] = etag
            if lastmod:
                headers["If-Modified-Since"] = lastmod

        t0 = time.time()
        for attempt in (0, 1):
            key = (p.scheme, p.netloc)
            reused = (not attempt) and key in self.conns
            conn = self._conn(p.scheme, p.netloc, timeout, fresh=bool(attempt))
            try:
                conn.request("GET", path, headers=headers)
                resp = conn.getresponse()
                raw = resp.read()             # must drain to reuse the socket
                break
            except (http.client.HTTPException, OSError) as exc:
                # A socket reaped by the server or a proxy looks exactly like
                # this. One silent retry on a brand-new connection, then give
                # up and let the Watcher record the failure.
                #
                # A timeout is TWO different failures and they need opposite
                # handling. Getting this wrong cost a night of false errors:
                #
                #   on a REUSED socket - the connection was idle and the server
                #     reaped it. Indistinguishable from a slow host until you
                #     retry, and retrying on a fresh socket succeeds. Suppressing
                #     this retry made katadata and idxchannel start "timing out"
                #     overnight when nothing was wrong with either.
                #   on a FRESH socket - the host really is slow or unreachable.
                #     Retrying spends a SECOND full timeout (45s becomes 90s) and
                #     starves every other source on the tick. Do not.
                try:
                    conn.close()
                except Exception:
                    pass
                self.conns.pop(key, None)
                if attempt or (isinstance(exc, TimeoutError) and not reused):
                    raise
        ms = int((time.time() - t0) * 1000)
        status = resp.status
        hdrs = {k.lower(): v for k, v in resp.getheaders()}

        if status in (301, 302, 303, 307, 308) and _depth < MAX_REDIRECTS:
            loc = hdrs.get("location")
            if loc:
                return self.get(urljoin(url, loc), timeout, conditional, _depth + 1)
        if status == 304:
            return Response(304, b"", hdrs, ms, url, not_modified=True)
        if status in (403, 429, 503):
            self.conns.pop((p.scheme, p.netloc), None)   # do not reuse a refused socket
            raise Refused(status, url)
        if status != 200:
            raise RuntimeError(f"HTTP {status} for {url}")

        body = _inflate(raw, hdrs.get("content-encoding"))
        if conditional:
            et, lm = hdrs.get("etag"), hdrs.get("last-modified")
            if et or lm:
                self.validators[url] = (et, lm)
        return Response(200, body, hdrs, ms, url)


def make_fetch(fetcher=None, conditional=True):
    """The callable jciengine.Watcher wants: source -> bytes, or None for 304."""
    f = fetcher or Fetcher()

    def fetch(source):
        r = f.get(source.url, timeout=getattr(source, "timeout", None),
                  conditional=conditional)
        return None if r.not_modified else r.body
    fetch.fetcher = f
    return fetch
