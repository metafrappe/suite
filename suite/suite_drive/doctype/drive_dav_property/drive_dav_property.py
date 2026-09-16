# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class DriveDAVProperty(Document):
    """A WebDAV dead property (RFC 4918 §4.4). All access goes through
    suite.suite_drive.webdav.deadprops — never through generic doc APIs."""
