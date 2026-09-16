import frappe
from frappe.tests import UnitTestCase
from lxml import etree

from suite.suite_drive.webdav.errors import BadRequest, UnsupportedMediaType
from suite.suite_drive.webdav.xmlutil import (
    MultistatusBuilder,
    dav,
    dav_element,
    parse_xml,
    serialize,
    status_line,
)


class TestWebDAVXml(UnitTestCase):
    def test_empty_body_is_none(self):
        self.assertIsNone(parse_xml(b""))
        self.assertIsNone(parse_xml(b"   \n"))

    def test_doctype_and_entities_rejected(self):
        xxe = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>'
        with self.assertRaises(BadRequest):
            parse_xml(xxe)

    def test_malformed_xml_rejected(self):
        with self.assertRaises(BadRequest):
            parse_xml(b"<unclosed>")

    def test_oversize_body_rejected(self):
        with self.assertRaises(UnsupportedMediaType):
            parse_xml(b"<a>" + b"x" * (1024 * 1024) + b"</a>")

    def test_parse_roundtrip_preserves_foreign_namespaces(self):
        body = b'<D:propfind xmlns:D="DAV:"><D:prop><z:foo xmlns:z="urn:z"/></D:prop></D:propfind>'
        root = parse_xml(body)
        self.assertEqual(root.tag, dav("propfind"))
        self.assertEqual(root[0][0].tag, "{urn:z}foo")

    def test_multistatus_shape(self):
        builder = MultistatusBuilder()
        response = builder.add_response("/dav/Home/")
        response.propstat(200, [dav_element("displayname", text="Home")])
        response.propstat(404, [dav_element("getetag")])
        builder.add_response("/dav/Home/x.txt").status(423)

        result = builder.build()
        self.assertEqual(result.status_code, 207)
        self.assertEqual(result.content_type, 'application/xml; charset="utf-8"')

        parsed = etree.fromstring(result.get_data())
        responses = parsed.findall(dav("response"))
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0].find(dav("href")).text, "/dav/Home/")
        statuses = [ps.find(dav("status")).text for ps in responses[0].findall(dav("propstat"))]
        self.assertEqual(statuses, ["HTTP/1.1 200 OK", "HTTP/1.1 404 Not Found"])
        self.assertEqual(responses[1].find(dav("status")).text, "HTTP/1.1 423 Locked")

    def test_empty_propstat_is_omitted(self):
        builder = MultistatusBuilder()
        builder.add_response("/dav/").propstat(404, [])
        parsed = etree.fromstring(builder.build().get_data())
        self.assertEqual(len(parsed.findall(f"{dav('response')}/{dav('propstat')}")), 0)

    def test_status_line(self):
        self.assertEqual(status_line(207), "HTTP/1.1 207 Multi-Status")
        self.assertEqual(status_line(507), "HTTP/1.1 507 Insufficient Storage")

    def test_serialize_declares_utf8(self):
        data = serialize(dav_element("multistatus"))
        self.assertTrue(data.startswith(b"<?xml version='1.0' encoding='utf-8'?>"))
