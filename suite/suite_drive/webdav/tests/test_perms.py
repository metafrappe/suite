import random
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from suite.suite_drive.api.permissions import get_user_access_for_user
from suite.suite_drive.utils import (
    GENERAL_USER,
    PERMISSION_TYPES,
    clear_user_group_cache,
    create_drive_file,
    generate_upward_path,
    get_root_folder,
    get_user_folder,
)
from suite.suite_drive.utils.files import FileManager
from suite.suite_drive.webdav import pathmap
from suite.suite_drive.webdav.perms import resolve_children_access
from suite.tests.utils import ensure_user

OWNER = "webdav-perms-owner@example.com"
READER = "webdav-perms-reader@example.com"
GROUP = "webdav-perms-group"


class TestBatchedPermissionResolver(IntegrationTestCase):
    """The batched Depth:1 resolver must never drift from the per-row
    get_user_access semantics — asserted over a randomized permission tree."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_user(OWNER)
        ensure_user(READER)
        if not frappe.db.exists("User Group", GROUP):
            frappe.get_doc(
                {"doctype": "User Group", "name": GROUP, "user_group_members": [{"user": READER}]}
            ).insert(ignore_permissions=True)
        clear_user_group_cache()

    def setUp(self):
        pathmap.reset_memo()

    def tearDown(self):
        frappe.set_user("Administrator")
        super().tearDown()

    def _build_random_tree(self, rng: random.Random, root: str, depth: int, owner: str) -> list[str]:
        """Folders + files with random Drive Permission rows; returns folder names."""
        manager = FileManager()
        folders = [root]
        with self.set_user(owner):
            frontier = [root]
            for _ in range(depth):
                next_frontier = []
                for parent in frontier:
                    for index in range(rng.randint(1, 3)):
                        folder = create_drive_file(
                            f"f{rng.randrange(10**6)}-{index}",
                            parent,
                            "Folder",
                            lambda f: manager.create_folder(f),
                            owner=rng.choice([owner, READER]),
                        )
                        next_frontier.append(folder.name)
                        folders.append(folder.name)
                    for index in range(rng.randint(0, 2)):
                        create_drive_file(
                            f"doc{rng.randrange(10**6)}-{index}.txt",
                            parent,
                            "Text",
                            f"/x/{rng.randrange(10**6)}",
                            "text/plain",
                            10,
                            owner=rng.choice([owner, READER]),
                        )
                frontier = next_frontier

        principals = [READER, f"$GROUP:{GROUP}", GENERAL_USER, ""]
        for node in folders + self._all_children(folders):
            if rng.random() < 0.45:
                for principal in rng.sample(principals, rng.randint(1, 2)):
                    bits = {ptype: rng.randint(0, 1) for ptype in PERMISSION_TYPES}
                    if not any(bits.values()):
                        bits["read"] = 1
                    if not frappe.db.exists("Drive Permission", {"entity": node, "user": principal}):
                        frappe.get_doc(
                            {
                                "doctype": "Drive Permission",
                                "entity": node,
                                "user": principal,
                                "deny": 1 if rng.random() < 0.3 else 0,
                                **bits,
                            }
                        ).insert(ignore_permissions=True)
        return folders

    def _all_children(self, folders: list[str]) -> list[str]:
        return frappe.get_all("File", filters={"folder": ["in", folders]}, pluck="name")

    def _assert_equivalent(self, folders: list[str], user: str):
        for folder in folders:
            children = pathmap.list_children(folder)
            if not children:
                continue
            parent_path = generate_upward_path(folder, user)
            batched = resolve_children_access(parent_path, children, user)
            for child in children:
                expected = get_user_access_for_user(child, user)
                actual = batched[child.name]
                for ptype in PERMISSION_TYPES:
                    self.assertEqual(
                        actual[ptype],
                        expected[ptype],
                        f"{ptype} drift on {child.file_name} ({child.name}) in {folder} for {user}",
                    )

    def test_equivalence_on_randomized_shared_tree(self):
        rng = random.Random(42)
        with self.set_user(OWNER):
            base = create_drive_file(
                f"perm-tree-{frappe.generate_hash(length=6)}",
                get_root_folder().name,
                "Folder",
                lambda f: FileManager().create_folder(f),
            )
        folders = self._build_random_tree(rng, base.name, depth=3, owner=OWNER)

        for user in (READER, "Guest"):
            self._assert_equivalent(folders, user)

    def test_equivalence_on_home_tree(self):
        rng = random.Random(1337)
        home = get_user_folder(OWNER).name
        folders = self._build_random_tree(rng, home, depth=2, owner=OWNER)
        self._assert_equivalent(folders, READER)

    def test_admin_short_circuit(self):
        with self.set_user(OWNER):
            base = create_drive_file(
                f"admin-tree-{frappe.generate_hash(length=6)}",
                get_user_folder(OWNER).name,
                "Folder",
                lambda f: FileManager().create_folder(f),
            )
            create_drive_file("secret.txt", base.name, "Text", "/x/s", "text/plain", 5)

        children = pathmap.list_children(base.name)
        access = resolve_children_access([], children, "Administrator")
        self.assertTrue(all(access[child.name]["write"] for child in children))

    def test_query_count_is_constant_in_child_count(self):
        with self.set_user(OWNER):
            base = create_drive_file(
                f"budget-tree-{frappe.generate_hash(length=6)}",
                get_user_folder(OWNER).name,
                "Folder",
                lambda f: FileManager().create_folder(f),
            )
            for index in range(60):
                create_drive_file(f"file-{index}.txt", base.name, "Text", f"/x/{index}", "text/plain", 1)

        children = pathmap.list_children(base.name)
        self.assertEqual(len(children), 60)
        parent_path = generate_upward_path(base.name, READER)

        with patch.object(frappe.db, "sql", wraps=frappe.db.sql) as sql:
            resolve_children_access(parent_path, children, READER)
        self.assertLessEqual(sql.call_count, 2, "batched resolver must not scale queries with children")
