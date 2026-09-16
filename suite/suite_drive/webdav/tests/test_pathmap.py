import frappe
from frappe.tests import IntegrationTestCase
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from suite.suite_drive.utils import ROOT_FOLDER, create_drive_file, get_user_folder
from suite.suite_drive.utils.files import FileManager
from suite.suite_drive.webdav import pathmap
from suite.suite_drive.webdav.errors import BadGateway, BadRequest, Forbidden
from suite.tests.utils import ensure_user

USER = "webdav-pathmap@example.com"


def make_folder(parent: str, name: str):
    manager = FileManager()
    return create_drive_file(name, parent, "Folder", lambda f: manager.create_folder(f))


def make_file(parent, name: str, **kwargs):
    parent_url = frappe.db.get_value("File", parent, "file_url") or ""
    return create_drive_file(
        name,
        parent,
        kwargs.pop("file_type", "Text"),
        f"{parent_url}/{frappe.generate_hash(length=8)}",
        kwargs.pop("mime_type", "text/plain"),
        kwargs.pop("file_size", 4),
        **kwargs,
    )


def resolve(path: str, user: str = USER):
    pathmap.reset_memo()
    return pathmap.resolve([segment for segment in path.split("/") if segment], user)


class TestWebDAVPathmap(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_user(USER)
        with cls.set_user(USER):
            cls.home = get_user_folder(USER).name
            cls.docs = make_folder(cls.home, "Docs")
            cls.report = make_file(cls.docs.name, "Report.txt")

    def setUp(self):
        frappe.set_user(USER)
        pathmap.reset_memo()

    def tearDown(self):
        frappe.set_user("Administrator")
        super().tearDown()

    def test_virtual_root_and_mounts(self):
        root = resolve("")
        self.assertTrue(root.is_mount)
        self.assertTrue(root.is_collection)
        self.assertIsNone(root.entity)

        home = resolve("Home")
        self.assertTrue(home.is_mount)
        self.assertEqual(home.entity.name, self.home)
        self.assertEqual(home.root, "home")

        # mount aliases are case-insensitive (Windows re-cases path components)
        self.assertEqual(resolve("hOmE").entity.name, self.home)
        self.assertEqual(resolve("everyone").entity.name, ROOT_FOLDER)

    def test_unknown_mount(self):
        result = resolve("Nowhere/file.txt")
        self.assertEqual(result.root, "unknown")
        self.assertFalse(result.exists)
        self.assertTrue(result.missing_intermediate)

    def test_nested_resolution(self):
        result = resolve("Home/Docs/Report.txt")
        self.assertEqual(result.entity.name, self.report.name)
        self.assertFalse(result.is_collection)

        folder = resolve("Home/Docs")
        self.assertEqual(folder.entity.name, self.docs.name)
        self.assertTrue(folder.is_collection)

    def test_missing_leaf_keeps_parent(self):
        result = resolve("Home/Docs/new-file.bin")
        self.assertFalse(result.exists)
        self.assertEqual(result.parent.name, self.docs.name)
        self.assertFalse(result.missing_intermediate)

    def test_missing_intermediate(self):
        result = resolve("Home/NoSuchFolder/file.txt")
        self.assertFalse(result.exists)
        self.assertIsNone(result.parent)
        self.assertTrue(result.missing_intermediate)

    def test_case_sensitivity_with_fallback(self):
        lower = make_file(self.docs.name, "report.txt")
        try:
            # exact case wins when both spellings exist
            self.assertEqual(resolve("Home/Docs/Report.txt").entity.name, self.report.name)
            self.assertEqual(resolve("Home/Docs/report.txt").entity.name, lower.name)
            # ambiguous case-variant lookup resolves nothing
            self.assertFalse(resolve("Home/Docs/REPORT.TXT").exists)
        finally:
            lower.delete(ignore_permissions=True, force=True)

        # unambiguous case-variant lookup falls back case-insensitively
        self.assertEqual(resolve("Home/Docs/REPORT.TXT").entity.name, self.report.name)

    def test_exact_duplicates_resolve_oldest_and_list_once(self):
        younger = make_file(self.docs.name, "Report.txt")
        try:
            self.assertEqual(resolve("Home/Docs/Report.txt").entity.name, self.report.name)
            listed = [row.name for row in pathmap.list_children(self.docs.name)]
            self.assertIn(self.report.name, listed)
            self.assertNotIn(younger.name, listed)
        finally:
            younger.delete(ignore_permissions=True, force=True)

    def test_unrepresentable_entities_are_invisible(self):
        link = make_file(self.docs.name, "Site.link", file_type="Link", mime_type="link")
        doc = make_file(self.docs.name, "Notes")
        doc.db_set({"content_doctype": "User", "content_docname": USER}, update_modified=False)
        slashed = make_file(self.docs.name, "before-rename")
        frappe.db.set_value("File", slashed.name, "file_name", "a/b.txt", update_modified=False)
        backslashed = make_file(self.docs.name, "before-bs-rename")
        frappe.db.set_value("File", backslashed.name, "file_name", "a\\b.txt", update_modified=False)
        percented = make_file(self.docs.name, "100%.txt")
        try:
            self.assertFalse(resolve("Home/Docs/Site.link").exists)
            self.assertFalse(resolve("Home/Docs/Notes").exists)
            listed = {row.name for row in pathmap.list_children(self.docs.name)}
            self.assertNotIn(link.name, listed)
            self.assertNotIn(doc.name, listed)
            self.assertNotIn(slashed.name, listed)
            # the LIKE-escape trap: '%\\%' matched %-suffixed names, not backslashes
            self.assertNotIn(backslashed.name, listed)
            self.assertIn(percented.name, listed)
        finally:
            frappe.db.set_value("File", slashed.name, "file_name", "before-rename", update_modified=False)
            frappe.db.set_value(
                "File", backslashed.name, "file_name", "before-bs-rename", update_modified=False
            )
            doc.db_set({"content_doctype": None, "content_docname": None}, update_modified=False)
            for entity in (link, doc, slashed, backslashed, percented):
                entity.reload()
                entity.delete(ignore_permissions=True, force=True)

    def test_trashed_names_are_free(self):
        trashed = make_file(self.docs.name, "gone.txt")
        trashed.db_set("status", "Trashed", update_modified=False)
        self.assertFalse(resolve("Home/Docs/gone.txt").exists)

    def test_validate_dav_name(self):
        parent = frappe._dict(name=self.docs.name)
        pathmap.validate_dav_name("fine.txt", parent)
        pathmap.validate_dav_name(".DS_Store", parent)  # Finder writes these constantly

        for bad in ("", "a" * 141, "a/b", "a\\b", ".", "..", "ctl\x01"):
            with self.assertRaises(BadRequest):
                pathmap.validate_dav_name(bad, parent)

        with self.assertRaises(Forbidden):
            pathmap.validate_dav_name(".embeds", parent)

        drive_root = frappe._dict(name=ROOT_FOLDER)
        for reserved in (".trash", ".uploads", ".Thumbnails", "Users"):
            with self.assertRaises(Forbidden):
                pathmap.validate_dav_name(reserved, drive_root)

    def test_parse_destination(self):
        def request(destination=None, host="s2.localhost:8001"):
            headers = {"Destination": destination} if destination else {}
            builder = EnvironBuilder(method="MOVE", path="/dav/Home/a", headers=headers)
            builder.host = host
            return Request(builder.get_environ())

        segments, slash = pathmap.parse_destination(request("http://s2.localhost:8001/dav/Home/Caf%C3%A9/"))
        self.assertEqual(segments, ["Home", "Café"])
        self.assertTrue(slash)

        segments, slash = pathmap.parse_destination(request("/dav/Home/b.txt"))
        self.assertEqual(segments, ["Home", "b.txt"])
        self.assertFalse(slash)

        # a Host-rewriting proxy ($host) drops the port; only an explicit conflict rejects
        segments, _ = pathmap.parse_destination(
            request("http://s2.localhost:8001/dav/Home/c", host="s2.localhost")
        )
        self.assertEqual(segments, ["Home", "c"])
        with self.assertRaises(BadGateway):
            pathmap.parse_destination(request("http://s2.localhost:9999/dav/Home/x"))

        with self.assertRaises(BadGateway):
            pathmap.parse_destination(request("http://evil.example.com/dav/Home/x"))
        with self.assertRaises(BadGateway):
            pathmap.parse_destination(request("http://s2.localhost:8001/files/x"))
        with self.assertRaises(BadRequest):
            pathmap.parse_destination(request(None))

    def test_href_encoding(self):
        self.assertEqual(pathmap.href_for([], True), "/dav/")
        self.assertEqual(pathmap.href_for(["Home", "a b.txt"], False), "/dav/Home/a%20b.txt")
        self.assertEqual(pathmap.href_for(["Home", "Café"], True), "/dav/Home/Caf%C3%A9/")
