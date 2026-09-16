from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from lxml import etree

from suite.suite_drive.utils import create_drive_file, get_root_folder, get_user_folder
from suite.suite_drive.utils.files import FileManager
from suite.suite_drive.webdav import propfind
from suite.suite_drive.webdav.errors import BadRequest, Forbidden, NotFoundError
from suite.suite_drive.webdav.tests.utils import ensure_user_with_password, make_ctx, write_file_fixture
from suite.suite_drive.webdav.xmlutil import dav
from suite.tests.utils import ensure_user

OWNER = "webdav-propfind-owner@example.com"
READER = "webdav-propfind-reader@example.com"
PASSWORD = "webdav-propfind-pw"


def multistatus(response) -> etree._Element:
    return etree.fromstring(response.get_data())


def hrefs(parsed) -> list[str]:
    return [element.text for element in parsed.findall(f"{dav('response')}/{dav('href')}")]


def propfind_response(user: str, path: str, depth: str = "1", body: bytes = b""):
    ctx = make_ctx("PROPFIND", path, user, headers={"Depth": depth}, data=body)
    return propfind.handle(ctx)


class TestWebDAVPropfind(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_user_with_password(OWNER, PASSWORD)
        ensure_user(READER)
        with cls.set_user(OWNER):
            cls.home = get_user_folder(OWNER).name
            manager = FileManager()
            cls.docs = create_drive_file("PropDocs", cls.home, "Folder", lambda f: manager.create_folder(f))
            cls.report = write_file_fixture(cls.docs.name, "report.txt", b"hello propfind")
            cls.notes = write_file_fixture(cls.docs.name, "notes.md", b"# notes", "text/markdown")

    def tearDown(self):
        frappe.set_user("Administrator")
        super().tearDown()

    def test_depth_infinity_is_refused_with_precondition(self):
        for depth_headers in ({"Depth": "infinity"}, {}):
            ctx = make_ctx("PROPFIND", "/dav/Home", OWNER, headers=depth_headers)
            with self.assertRaises(Forbidden) as caught:
                propfind.handle(ctx)
            self.assertEqual(caught.exception.condition, "propfind-finite-depth")

    def test_depth_zero_file_properties(self):
        parsed = multistatus(propfind_response(OWNER, "/dav/Home/PropDocs/report.txt", depth="0"))
        responses = parsed.findall(dav("response"))
        self.assertEqual(len(responses), 1)

        prop = responses[0].find(f"{dav('propstat')}/{dav('prop')}")
        self.assertEqual(prop.find(dav("displayname")).text, "report.txt")
        self.assertEqual(prop.find(dav("getcontentlength")).text, str(len(b"hello propfind")))
        self.assertEqual(prop.find(dav("getcontenttype")).text, "text/plain")
        self.assertTrue(prop.find(dav("getlastmodified")).text.endswith(" GMT"))
        self.assertTrue(prop.find(dav("getetag")).text.startswith('"'))
        self.assertEqual(len(prop.find(dav("resourcetype"))), 0)

    def test_depth_one_lists_children_with_collection_hrefs(self):
        parsed = multistatus(propfind_response(OWNER, "/dav/Home/PropDocs"))
        listed = hrefs(parsed)
        self.assertEqual(listed[0], "/dav/Home/PropDocs/")
        self.assertIn("/dav/Home/PropDocs/report.txt", listed)
        self.assertIn("/dav/Home/PropDocs/notes.md", listed)

    def test_depth_one_on_file_is_just_the_file(self):
        parsed = multistatus(propfind_response(OWNER, "/dav/Home/PropDocs/report.txt"))
        self.assertEqual(len(parsed.findall(dav("response"))), 1)

    def test_virtual_root_lists_mounts(self):
        parsed = multistatus(propfind_response(OWNER, "/dav"))
        self.assertEqual(hrefs(parsed), ["/dav/", "/dav/Home/", "/dav/Everyone/"])

    def test_unreadable_children_are_omitted(self):
        with self.set_user(OWNER):
            shared = create_drive_file(
                f"prop-shared-{frappe.generate_hash(length=6)}",
                get_root_folder().name,
                "Folder",
                lambda f: FileManager().create_folder(f),
            )
            write_file_fixture(shared.name, "visible.txt", b"ok")
            hidden = write_file_fixture(shared.name, "hidden.txt", b"no")
        frappe.get_doc(
            {"doctype": "Drive Permission", "entity": hidden.name, "user": READER, "read": 1, "deny": 1}
        ).insert(ignore_permissions=True)

        parsed = multistatus(propfind_response(READER, f"/dav/Everyone/{shared.file_name}"))
        listed = hrefs(parsed)
        self.assertIn(f"/dav/Everyone/{shared.file_name}/visible.txt", listed)
        self.assertNotIn(f"/dav/Everyone/{shared.file_name}/hidden.txt", listed)

    def test_unreadable_target_is_404(self):
        # READER has no path into OWNER's home
        home_name = frappe.db.get_value("File", self.home, "file_name")
        with self.assertRaises(NotFoundError):
            propfind_response(READER, f"/dav/Everyone/{home_name}")
        with self.assertRaises(NotFoundError):
            propfind_response(OWNER, "/dav/Home/PropDocs/no-such-file.bin")

    def test_propname_mode(self):
        body = b'<?xml version="1.0"?><D:propfind xmlns:D="DAV:"><D:propname/></D:propfind>'
        parsed = multistatus(propfind_response(OWNER, "/dav/Home/PropDocs/report.txt", "0", body))
        prop = parsed.find(f"{dav('response')}/{dav('propstat')}/{dav('prop')}")
        etag = prop.find(dav("getetag"))
        self.assertIsNotNone(etag)
        self.assertIsNone(etag.text)

    def test_prop_mode_reports_missing_as_404(self):
        body = (
            b'<?xml version="1.0"?><D:propfind xmlns:D="DAV:"><D:prop>'
            b'<D:getetag/><z:custom xmlns:z="urn:z"/></D:prop></D:propfind>'
        )
        parsed = multistatus(propfind_response(OWNER, "/dav/Home/PropDocs/report.txt", "0", body))
        propstats = parsed.findall(f"{dav('response')}/{dav('propstat')}")
        self.assertEqual(len(propstats), 2)
        self.assertIsNotNone(propstats[0].find(f"{dav('prop')}/{dav('getetag')}"))
        self.assertIn("404", propstats[1].find(dav("status")).text)
        self.assertIsNotNone(propstats[1].find(f"{dav('prop')}/{{urn:z}}custom"))

    def test_quota_props_only_when_requested(self):
        body = (
            b'<?xml version="1.0"?><D:propfind xmlns:D="DAV:"><D:prop>'
            b"<D:quota-used-bytes/><D:quota-available-bytes/></D:prop></D:propfind>"
        )
        parsed = multistatus(propfind_response(OWNER, "/dav/Home", "0", body))
        prop = parsed.find(f"{dav('response')}/{dav('propstat')}/{dav('prop')}")
        self.assertIsNotNone(prop.find(dav("quota-used-bytes")))

        # allprop must not include quota (RFC 4331)
        parsed = multistatus(propfind_response(OWNER, "/dav/Home", "0"))
        prop = parsed.find(f"{dav('response')}/{dav('propstat')}/{dav('prop')}")
        self.assertIsNone(prop.find(dav("quota-used-bytes")))

    def test_malformed_bodies_are_400(self):
        with self.assertRaises(BadRequest):
            propfind_response(OWNER, "/dav/Home", "0", b"<not-closed")
        with self.assertRaises(BadRequest):
            propfind_response(OWNER, "/dav/Home", "0", b"<wrong-root/>")

    def test_depth_one_query_budget(self):
        with self.set_user(OWNER):
            big = create_drive_file(
                f"prop-big-{frappe.generate_hash(length=6)}",
                self.home,
                "Folder",
                lambda f: FileManager().create_folder(f),
            )
            for index in range(40):
                create_drive_file(f"item-{index}.txt", big.name, "Text", f"/x/{index}", "text/plain", 1)

        big_name = frappe.db.get_value("File", big.name, "file_name")
        ctx = make_ctx("PROPFIND", f"/dav/Home/{big_name}", OWNER, headers={"Depth": "1"})
        with patch.object(frappe.db, "sql", wraps=frappe.db.sql) as sql:
            response = propfind.handle(ctx)
        self.assertEqual(response.status_code, 207)
        # resolution ≤4 + parent CTE + children + grants + dead props + locks = 9,
        # independent of child count
        self.assertLessEqual(sql.call_count, 9, "PROPFIND Depth:1 must stay O(1) in child count")
