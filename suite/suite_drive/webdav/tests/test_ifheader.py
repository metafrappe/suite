import frappe
from frappe.tests import UnitTestCase

from suite.suite_drive.webdav.ifheader import EMPTY_IF, BadIfHeader, parse_if_header

TOKEN = "urn:uuid:11111111-2222-3333-4444-555555555555"


def evaluate(header, entity="target", tokens=frozenset(), etag=None, href_map=None):
    parsed = parse_if_header(header)
    href_map = href_map or {}

    def resolve_href(href):
        if href is None:
            return entity
        return href_map.get(href)

    return parsed.evaluate(
        resolve_href,
        lambda name: etag if name == entity else None,
        lambda name: tokens if name == entity else frozenset(),
    )


class TestIfHeaderParsing(UnitTestCase):
    def test_empty_and_missing(self):
        self.assertIs(parse_if_header(None), EMPTY_IF)
        self.assertIs(parse_if_header("   "), EMPTY_IF)

    def test_no_tag_list_with_token(self):
        parsed = parse_if_header(f"(<{TOKEN}>)")
        self.assertEqual(parsed.all_tokens(), {TOKEN})
        group = parsed.tagged[0]
        self.assertIsNone(group.resource_href)
        self.assertFalse(group.lists[0].conditions[0].negated)

    def test_tagged_lists(self):
        header = f'<http://host/dav/a.txt> (<{TOKEN}>) </dav/b.txt> (["etag-b"])'
        parsed = parse_if_header(header)
        self.assertEqual(len(parsed.tagged), 2)
        self.assertEqual(parsed.tagged[0].resource_href, "http://host/dav/a.txt")
        self.assertEqual(parsed.tagged[1].resource_href, "/dav/b.txt")
        self.assertEqual(parsed.tagged[1].lists[0].conditions[0].etag, '"etag-b"')

    def test_not_and_multiple_conditions(self):
        parsed = parse_if_header(f'(Not <DAV:no-lock> ["tag"]) (<{TOKEN}>)')
        first = parsed.tagged[0].lists[0]
        self.assertTrue(first.conditions[0].negated)
        self.assertEqual(first.conditions[0].token, "DAV:no-lock")
        self.assertFalse(first.conditions[1].negated)
        # negated tokens still count as submitted
        self.assertEqual(parse_if_header("(Not <DAV:no-lock>)").all_tokens(), {"DAV:no-lock"})

    def test_corrupt_headers_raise(self):
        for bad in ("(", "()", "(<tok>", "Not <tok>", "((<tok>))", "garbage", "<href-only>"):
            with self.assertRaises(BadIfHeader, msg=bad):
                parse_if_header(bad)


class TestIfHeaderEvaluation(UnitTestCase):
    def test_token_match(self):
        self.assertTrue(evaluate(f"(<{TOKEN}>)", tokens=frozenset({TOKEN})))
        self.assertFalse(evaluate(f"(<{TOKEN}>)", tokens=frozenset()))

    def test_no_lock_tautology(self):
        self.assertTrue(evaluate("(Not <DAV:no-lock>)"))

    def test_etag_condition(self):
        self.assertTrue(evaluate('(["v1"])', etag='"v1"'))
        self.assertFalse(evaluate('(["v1"])', etag='"v2"'))

    def test_and_within_list(self):
        header = f'(<{TOKEN}> ["v1"])'
        self.assertTrue(evaluate(header, tokens=frozenset({TOKEN}), etag='"v1"'))
        self.assertFalse(evaluate(header, tokens=frozenset({TOKEN}), etag='"v2"'))
        self.assertFalse(evaluate(header, tokens=frozenset(), etag='"v1"'))

    def test_or_across_lists(self):
        header = f'(<{TOKEN}> ["wrong"]) (Not <DAV:no-lock>)'
        self.assertTrue(evaluate(header, tokens=frozenset({TOKEN}), etag='"right"'))

    def test_tagged_binding(self):
        header = f"</dav/other.txt> (<{TOKEN}>)"
        # token active on the request target but the condition binds to /dav/other.txt
        self.assertFalse(evaluate(header, tokens=frozenset({TOKEN}), href_map={"/dav/other.txt": "other"}))
        # unresolvable tagged href evaluates false rather than erroring
        self.assertFalse(evaluate(header, href_map={}))
