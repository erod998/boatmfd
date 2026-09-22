"""Tests for the offline asset guard.

    python -m unittest discover -s tests -t . -v

The first test is the one that matters operationally: it fails the suite if anything the
browser loads points off the boat. The rest pin down the scanner itself, because a guard
that cries wolf gets switched off and a guard that misses the real thing is decoration.
"""
import tempfile
import unittest
from pathlib import Path

from app.offline_check import Finding, host_of, is_external, report, scan_static

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"


def tree(**files):
    """A throwaway static/ tree. Keys are posix paths, '__' in a name means '/'."""
    root = Path(tempfile.mkdtemp())
    for name, body in files.items():
        path = root / name.replace("__", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


def urls(findings):
    return [f.url for f in findings]


class TestShippedStatic(unittest.TestCase):
    """The gate. If this fails, the dashboard has a dependency the lake will not satisfy."""

    def test_nothing_under_static_needs_the_internet(self):
        findings = scan_static(STATIC_DIR)
        self.assertEqual(findings, [], "\n".join(
            ["These load from the internet, which the boat does not have:"]
            + [f"  {f}" for f in findings]
            + ["Vendor them under static/vendor/ -- see static/vendor/README.md."]))

    def test_leaflet_is_actually_served_locally(self):
        # The guard above passes trivially if the page stops loading Leaflet at all, so pin
        # the positive: it is referenced, and from our own origin.
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn("/static/vendor/leaflet.js", html)
        self.assertIn("/static/vendor/leaflet.css", html)
        self.assertIn("/static/vendor/leaflet-rotate.js", html)
        for name in ("leaflet.js", "leaflet.css", "leaflet-rotate.js"):
            self.assertTrue((STATIC_DIR / "vendor" / name).is_file(), f"{name} is referenced but missing")


class TestTheRegression(unittest.TestCase):
    """The exact shape of the bug this exists for: Leaflet loaded from a CDN."""

    def test_catches_the_cdn_leaflet_the_boat_shipped_with(self):
        root = tree(**{"index.html": (
            '<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css" />\n'
            '<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>\n'
            '<script src="https://cdn.jsdelivr.net/npm/leaflet-rotate@0.2.8/dist/leaflet-rotate-src.js"></script>\n'
        )})
        findings = scan_static(root)
        self.assertEqual(len(findings), 3)
        self.assertEqual([f.line for f in findings], [1, 2, 3])
        self.assertEqual({f.host for f in findings}, {"cdnjs.cloudflare.com", "cdn.jsdelivr.net"})


class TestHtml(unittest.TestCase):
    def test_flags_script_and_stylesheet_from_a_cdn(self):
        root = tree(**{"index.html":
            '<link href="https://unpkg.com/x.css">\n<script src="https://unpkg.com/x.js"></script>'})
        self.assertEqual(len(scan_static(root)), 2)

    def test_allows_our_own_paths(self):
        root = tree(**{"index.html":
            '<script src="/static/js/app.js"></script><link href="css/style.css">'
            '<a href="#helm">x</a><img src="data:image/png;base64,AAAA">'})
        self.assertEqual(scan_static(root), [])

    def test_allows_the_pi_serving_itself(self):
        root = tree(**{"index.html": '<script src="http://localhost:8090/static/js/app.js"></script>'})
        self.assertEqual(scan_static(root), [])

    def test_flags_protocol_relative_urls(self):
        root = tree(**{"index.html": '<script src="//cdn.jsdelivr.net/npm/x.js"></script>'})
        self.assertEqual(urls(scan_static(root)), ["//cdn.jsdelivr.net/npm/x.js"])

    def test_reads_every_candidate_in_a_srcset(self):
        root = tree(**{"index.html": '<img srcset="local.png 1x, https://unpkg.com/big.png 2x">'})
        self.assertEqual(urls(scan_static(root)), ["https://unpkg.com/big.png"])

    def test_reports_the_line_the_reference_is_on(self):
        root = tree(**{"index.html": '\n\n\n<script src="https://unpkg.com/x.js"></script>'})
        self.assertEqual(scan_static(root)[0].line, 4)


class TestCss(unittest.TestCase):
    def test_flags_an_external_url_and_import(self):
        root = tree(**{"css__style.css":
            '@import "https://fonts.googleapis.com/css?family=X";\n'
            '.a { background: url("https://unpkg.com/bg.png"); }'})
        self.assertEqual({f.host for f in scan_static(root)}, {"fonts.googleapis.com", "unpkg.com"})

    def test_allows_relative_and_inline_assets(self):
        root = tree(**{"css__style.css":
            '.a { background: url(images/layers.png); }\n'
            '.b { background: url("data:image/gif;base64,R0lGOD"); }'})
        self.assertEqual(scan_static(root), [])

    def test_vendor_stylesheets_are_scanned_in_full(self):
        # Leaflet's own CSS is relative (url(images/...)), but a vendored sheet pulling a
        # webfont would be a genuine load and has to fail.
        root = tree(**{"vendor__leaflet.css": '@import url(https://fonts.gstatic.com/f.woff2);'})
        self.assertEqual(urls(scan_static(root)), ["https://fonts.gstatic.com/f.woff2"])


class TestJavaScript(unittest.TestCase):
    def test_flags_a_url_in_a_string_literal(self):
        root = tree(**{"js__app.js": 'const API = "https://example.com/v1";\nfetch(API);'})
        self.assertEqual(urls(scan_static(root)), ["https://example.com/v1"])

    def test_ignores_urls_in_comments(self):
        # The false positive that would get this guard disabled: every file here cites its
        # sources in prose.
        root = tree(**{"js__app.js":
            "// See https://leafletjs.com/reference.html for the options below.\n"
            "/* Ported from https://github.com/someone/repo/blob/main/x.js */\n"
            "const z = 1;"})
        self.assertEqual(scan_static(root), [])

    def test_ignores_the_svg_namespace(self):
        # chrome.js and dials.js both declare it; xmlns identifies a spec, it is never fetched.
        root = tree(**{"js__dials.js":
            'const SVGNS = "http://www.w3.org/2000/svg";\n'
            'const t = `<svg xmlns="http://www.w3.org/2000/svg"></svg>`;'})
        self.assertEqual(scan_static(root), [])

    def test_reads_all_three_kinds_of_string_literal(self):
        root = tree(**{"js__app.js":
            'a("https://a.example/1");\n'
            "b('https://b.example/2');\n"
            "c(`https://c.example/3`);"})
        self.assertEqual({f.host for f in scan_static(root)}, {"a.example", "b.example", "c.example"})

    def test_does_not_mistake_a_bare_double_slash_for_a_host(self):
        root = tree(**{"js__app.js": 'const p = "//static/js";\nconst q = "a // b";'})
        self.assertEqual(scan_static(root), [])

    def test_vendor_js_doc_links_are_not_flagged(self):
        # Minified third-party source carries repo and MDN links in its string tables. Every
        # one of them reported would bury the one that matters.
        root = tree(**{"vendor__leaflet.js":
            't.url="https://github.com/Leaflet/Leaflet";e="https://developer.mozilla.org/x";'})
        self.assertEqual(scan_static(root), [])

    def test_vendor_js_pulling_from_a_cdn_is_flagged(self):
        # A vendored library quietly fetching a dependency at runtime is the real hazard, and
        # is exactly as fatal offline as the CDN <script> tag was.
        root = tree(**{"vendor__plugin.js": 'load("https://cdn.jsdelivr.net/npm/dep@1/dist/dep.js");'})
        self.assertEqual(urls(scan_static(root)), ["https://cdn.jsdelivr.net/npm/dep@1/dist/dep.js"])


class TestScanScope(unittest.TestCase):
    def test_ignores_files_the_browser_does_not_parse_for_urls(self):
        root = tree(**{"vendor__README.md": "Source: https://cdnjs.cloudflare.com/x.js",
                       "data.json": '{"u": "https://unpkg.com/x"}'})
        self.assertEqual(scan_static(root), [])

    def test_walks_subdirectories(self):
        root = tree(**{"js__deep__nested__x.js": 'f("https://unpkg.com/x");'})
        self.assertEqual(scan_static(root)[0].path, "js/deep/nested/x.js")

    def test_findings_come_back_in_file_order(self):
        root = tree(**{"a.html": '<script src="https://unpkg.com/a.js"></script>',
                       "z.html": '<script src="https://unpkg.com/z.js"></script>'})
        self.assertEqual([f.path for f in scan_static(root)], ["a.html", "z.html"])


class TestUrlClassification(unittest.TestCase):
    def test_hosts(self):
        for url, expected in [
            ("https://cdn.jsdelivr.net/npm/x", "cdn.jsdelivr.net"),
            ("HTTP://Unpkg.COM/x", "unpkg.com"),
            ("//fonts.gstatic.com/f.woff2", "fonts.gstatic.com"),
            ("http://localhost:8090/x", "localhost"),
            ("/static/js/app.js", ""),
            ("images/layers.png", ""),
        ]:
            with self.subTest(url=url):
                self.assertEqual(host_of(url), expected)

    def test_external_or_not(self):
        for url, external in [
            ("https://unpkg.com/x.js", True),
            ("//unpkg.com/x.js", True),
            ("http://localhost:8090/x.js", False),
            ("http://127.0.0.1/x.js", False),
            ("/static/js/app.js", False),
            ("css/style.css", False),
            ("", False),
            ("#helm", False),
            ("data:image/png;base64,AAAA", False),
            ("blob:abc", False),
            ("mailto:someone@example.com", False),
            ("http://www.w3.org/2000/svg", False),
        ]:
            with self.subTest(url=url):
                self.assertEqual(is_external(url), external)


class TestReport(unittest.TestCase):
    def test_says_nothing_when_the_tree_is_clean(self):
        lines = []
        self.assertEqual(report(tree(**{"index.html": '<script src="/static/js/app.js"></script>'}), lines.append), [])
        self.assertEqual(lines, [])

    def test_names_the_file_line_and_url_it_found(self):
        lines = []
        root = tree(**{"index.html": '<script src="https://cdn.jsdelivr.net/npm/x.js"></script>'})
        self.assertEqual(len(report(root, lines.append)), 1)
        body = "\n".join(lines)
        self.assertIn("static/index.html:1", body)
        self.assertIn("https://cdn.jsdelivr.net/npm/x.js", body)
        self.assertIn("static/vendor/", body)   # says what to do about it
        self.assertTrue(all(line.startswith("[offline]") for line in lines))


class TestFinding(unittest.TestCase):
    def test_reads_as_a_clickable_location(self):
        self.assertEqual(str(Finding("js/app.js", 12, "https://unpkg.com/x", "unpkg.com")),
                         "static/js/app.js:12 -> https://unpkg.com/x")


if __name__ == "__main__":
    unittest.main()
