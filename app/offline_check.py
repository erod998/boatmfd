"""Guards the one assumption the whole dashboard rests on: there is no internet.

The boat gets dock WiFi occasionally and nothing at all on the water. Anything the UI
loads from a CDN therefore works perfectly in testing -- where the machine is online, or
where the browser still has yesterday's copy cached -- and is simply absent on the lake.

That is exactly how Leaflet itself shipped from cdnjs for as long as it did. Every test
passed, the chart drew correctly, and a reload out of range would have produced no map at
all: with no `L`, app.js throws on its first `L.map()` call and the whole chart screen
dies. The failure is silent, it only appears where it cannot be fixed, and nothing else
in the suite was looking for it.

So this scans the files the browser actually loads and names anything pointing off the
boat. tests/test_offline_check.py turns it into a hard gate, which is where it does the
real work -- a CDN reference fails the suite long before it can reach the Pi. The startup
pass covers what the test cannot see: a file hand-edited over SSH, or restored from an
old backup. It only prints. Refusing to boot the helm display over a lint finding would
be a worse bug than the one being guarded against.
"""
import re
from dataclasses import dataclass
from pathlib import Path

# xmlns="..." identifies a spec; the browser never fetches it. Both of ours are the SVG
# namespace, in chrome.js and dials.js.
NAMESPACE_URLS = frozenset({
    "http://www.w3.org/2000/svg",
    "http://www.w3.org/1999/xlink",
    "http://www.w3.org/1999/xhtml",
    "http://www.w3.org/2000/xmlns/",
    "http://www.w3.org/XML/1998/namespace",
})

# Served by the Pi itself, so they are reachable with the boat completely isolated.
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "[::1]", "::1"})

# Used only to judge vendored third-party JS, where doc links in comments are unavoidable
# noise but a live CDN reference is the precise thing worth failing over.
CDN_HOSTS = frozenset({
    "ajax.googleapis.com", "cdn.jsdelivr.net", "cdn.skypack.dev", "cdnjs.cloudflare.com",
    "code.jquery.com", "esm.sh", "fonts.googleapis.com", "fonts.gstatic.com",
    "maxcdn.bootstrapcdn.com", "stackpath.bootstrapcdn.com", "unpkg.com",
})

SCANNED_SUFFIXES = (".html", ".css", ".js")

_NON_FETCHED_SCHEMES = ("#", "data:", "blob:", "about:", "mailto:", "tel:", "javascript:")

_HTML_ATTR = re.compile(r"""\b(?:src|srcset|href|poster|data-src)\s*=\s*["']([^"']+)["']""", re.I)
_CSS_URL = re.compile(r"""url\(\s*['"]?([^'")]+)""", re.I)
_CSS_IMPORT = re.compile(r"""@import\s+(?:url\(\s*)?['"]([^'"]+)""", re.I)

# Only string literals, so a URL in a comment -- a link to documentation, or this very
# docstring -- is not mistaken for something the page loads.
_JS_STRING = re.compile(
    r'"((?:[^"\\\n]|\\.)*)"'
    r"|'((?:[^'\\\n]|\\.)*)'"
    r"|`((?:[^`\\]|\\.)*)`",
    re.S,
)
# A bare "//" is far too common (paths, protocol comments) to treat as a host, so require
# something that actually looks like one: a dot and a TLD.
_URL_IN_STRING = re.compile(r"(?:https?:)?//[A-Za-z0-9.-]+\.[A-Za-z]{2,}[^\s'\"`\\]*")


@dataclass(frozen=True)
class Finding:
    """One reference that needs the internet, and where to find it."""
    path: str   # relative to static/, posix-style
    line: int
    url: str
    host: str

    def __str__(self):
        return f"static/{self.path}:{self.line} -> {self.url}"


def host_of(url):
    """The hostname of an absolute or protocol-relative URL, lowercased ('' if neither)."""
    u = url.strip()
    if u.startswith("//"):
        rest = u[2:]
    elif u.lower().startswith(("http://", "https://")):
        rest = u.split("//", 1)[1]
    else:
        return ""
    host = rest.split("/")[0].split("?")[0].split("#")[0]
    return host.split("@")[-1].rsplit(":", 1)[0].lower() if "]" not in host else host.lower()


def is_external(url):
    """True when fetching `url` would need a working internet connection."""
    u = url.strip()
    if not u or u.startswith(_NON_FETCHED_SCHEMES) or u in NAMESPACE_URLS:
        return False
    host = host_of(u)
    return bool(host) and host not in LOCAL_HOSTS


def _scan_html(text):
    for m in _HTML_ATTR.finditer(text):
        # srcset carries several candidates with size descriptors: "a.png 1x, b.png 2x".
        for candidate in m.group(1).split(","):
            url = candidate.strip().split(" ")[0]
            if url:
                yield m.start(1), url


def _scan_css(text):
    for pattern in (_CSS_URL, _CSS_IMPORT):
        for m in pattern.finditer(text):
            yield m.start(1), m.group(1).strip()


def _scan_js(text):
    for m in _JS_STRING.finditer(text):
        literal = next((g for g in m.groups() if g is not None), "")
        if not literal:
            continue
        base = m.start(1) if m.group(1) is not None else m.start(2) if m.group(2) is not None else m.start(3)
        for hit in _URL_IN_STRING.finditer(literal):
            yield base + hit.start(), hit.group(0)


def _line_of(text, offset):
    return text.count("\n", 0, offset) + 1


def scan_static(static_dir):
    """Every reference under `static_dir` that would fail with no internet, in file order."""
    root = Path(static_dir)
    findings = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        suffix = path.suffix.lower()
        if suffix not in SCANNED_SUFFIXES:
            continue
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        scan = {".html": _scan_html, ".css": _scan_css, ".js": _scan_js}[suffix]
        # Vendored libraries are minified third-party source: their string tables are full
        # of doc and repo links that are never fetched, so flagging every one would bury
        # the signal. A CDN host in there is the real thing -- a library quietly pulling a
        # dependency at runtime -- and is still reported. Their stylesheets are scanned in
        # full, because a CSS url() or @import genuinely is a load.
        vendor_js = suffix == ".js" and (rel == "vendor" or rel.startswith("vendor/"))
        for offset, url in scan(text):
            if not is_external(url):
                continue
            host = host_of(url)
            if vendor_js and host not in CDN_HOSTS:
                continue
            findings.append(Finding(rel, _line_of(text, offset), url, host))
    return findings


def report(static_dir, echo=print):
    """Prints any findings in the systemd journal's voice. Returns them, for the caller."""
    findings = scan_static(static_dir)
    if not findings:
        return findings
    echo(f"[offline] {len(findings)} reference(s) under static/ point off the boat. "
         f"These work at the dock and FAIL on the water:")
    for finding in findings:
        echo(f"[offline]   {finding}")
    echo("[offline] Vendor them under static/vendor/ and serve them locally "
         "(see static/vendor/README.md).")
    return findings
