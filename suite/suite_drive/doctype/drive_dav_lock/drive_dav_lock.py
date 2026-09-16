# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class DriveDAVLock(Document):
    """A WebDAV write lock (RFC 4918 §6). DB-backed so it survives gunicorn
    worker recycling; all protocol access goes through suite.suite_drive.webdav.locks."""
