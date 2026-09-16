from datetime import datetime

import frappe
from frappe.tests import IntegrationTestCase

from suite.suite_drive.webdav.properties import compute_etag, iso8601, live_properties, rfc1123
from suite.suite_drive.webdav.xmlutil import dav


def row(**overrides):
    base = frappe._dict(
        name="abc123",
        file_name="report.pdf",
        file_size=2048,
        mime_type="application/pdf",
        content_hash=None,
        modified=datetime(2026, 8, 24, 10, 30, 0, 123456),
        creation=datetime(2026, 8, 1, 9, 0, 0),
    )
    base.update(overrides)
    return base


class TestWebDAVProperties(IntegrationTestCase):
    def test_etag_prefers_content_hash(self):
        self.assertEqual(compute_etag(row(content_hash="deadbeef")), '"sha256-deadbeef"')
        long_hash = "ab" * 32
        self.assertEqual(compute_etag(row(content_hash=long_hash)), f'"sha256-{long_hash[:32]}"')

    def test_etag_fallback_is_stable_and_strong_quoted(self):
        first, second = compute_etag(row()), compute_etag(row())
        self.assertEqual(first, second)
        self.assertTrue(first.startswith('"') and first.endswith('"'))
        self.assertIn("abc123-2048-", first)
        # any mtime or size change must change the etag
        self.assertNotEqual(first, compute_etag(row(file_size=1)))
        self.assertNotEqual(first, compute_etag(row(modified=datetime(2026, 8, 24, 10, 30, 1))))

    def test_date_formats(self):
        stamp = rfc1123(datetime(2026, 8, 24, 10, 30, 0))
        self.assertTrue(stamp.endswith(" GMT"))
        self.assertIn("2026", stamp)
        self.assertRegex(iso8601(datetime(2026, 8, 24, 10, 30, 0)), r"^2026-08-2\dT\d\d:\d\d:\d\dZ$")

    def test_file_properties(self):
        props = live_properties(row(), is_collection=False, display_name="report.pdf")
        self.assertEqual(props[dav("displayname")].text, "report.pdf")
        self.assertEqual(props[dav("getcontentlength")].text, "2048")
        self.assertEqual(props[dav("getcontenttype")].text, "application/pdf")
        self.assertIsNotNone(props[dav("getetag")])
        self.assertEqual(len(props[dav("resourcetype")]), 0)
        self.assertIsNone(props[dav("quota-used-bytes")])

    def test_collection_properties(self):
        props = live_properties(row(), is_collection=True, display_name="Docs", quota=(500, 1000))
        # folders carry rolled-up sizes Drive-side; content-length is not defined for them
        self.assertIsNone(props[dav("getcontentlength")])
        self.assertIsNone(props[dav("getetag")])
        self.assertIsNone(props[dav("getcontenttype")])
        self.assertEqual(len(props[dav("resourcetype")]), 1)
        self.assertEqual(props[dav("resourcetype")][0].tag, dav("collection"))
        self.assertEqual(props[dav("quota-used-bytes")].text, "500")
        self.assertEqual(props[dav("quota-available-bytes")].text, "500")

    def test_unlimited_quota_omits_available(self):
        props = live_properties(row(), is_collection=True, display_name="Docs", quota=(500, 0))
        self.assertEqual(props[dav("quota-used-bytes")].text, "500")
        self.assertIsNone(props[dav("quota-available-bytes")])

    def test_virtual_root_has_no_dates(self):
        props = live_properties(None, is_collection=True, display_name="Frappe Drive")
        self.assertIsNone(props[dav("getlastmodified")])
        self.assertIsNone(props[dav("creationdate")])
        self.assertEqual(len(props[dav("resourcetype")]), 1)
