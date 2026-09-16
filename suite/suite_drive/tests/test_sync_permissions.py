import unittest
from unittest.mock import Mock, patch

import frappe

from suite.suite_drive.api.scripts import sync_from_disk, sync_preview


class TestSyncPermissions(unittest.TestCase):
    """`sync_preview` lists files that have no `File` record yet, so no share or
    folder permission can filter them. Only a site admin may read that listing."""

    def _run(self, fn, is_admin):
        with patch("suite.suite_drive.api.scripts.is_drive_site_admin", return_value=is_admin):
            # Patched so the test asserts on the permission gate, not on disk state.
            with patch("suite.suite_drive.api.scripts.FileManager") as manager:
                manager.return_value.fetch_new_files.return_value = {}
                return fn()

    def test_sync_preview_is_refused_without_admin(self):
        with self.assertRaises(frappe.PermissionError):
            self._run(sync_preview, is_admin=False)

    def test_sync_preview_is_allowed_for_admin(self):
        self.assertEqual(list(self._run(sync_preview, is_admin=True)), [])

    def test_sync_from_disk_is_still_refused_without_admin(self):
        with self.assertRaises(frappe.PermissionError):
            self._run(sync_from_disk, is_admin=False)

    def test_sync_preview_does_not_touch_storage_before_refusing(self):
        """The refusal must precede `FileManager()`, so a non-admin request never
        reaches the filesystem or S3 at all."""
        with patch("suite.suite_drive.api.scripts.is_drive_site_admin", return_value=False):
            with patch("suite.suite_drive.api.scripts.FileManager") as manager:
                with self.assertRaises(frappe.PermissionError):
                    sync_preview()
        manager.assert_not_called()
