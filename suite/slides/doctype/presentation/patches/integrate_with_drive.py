import frappe

from suite.suite_drive.utils import create_drive_file, get_user_folder


def execute():
    """Back presentations with Drive Files; move the legacy `is_public` flag to Drive permissions."""
    public = set()
    if frappe.db.has_column("Presentation", "is_public"):
        public = set(frappe.db.sql_list("SELECT name FROM `tabPresentation` WHERE is_public = 1"))

    frappe.flags.mute_drive_activity_log = True
    try:
        presentations = frappe.get_all(
            "Presentation",
            filters={"is_template": 0},
            fields=["name", "title", "owner"],
        )
        for presentation in presentations:
            file = frappe.db.get_value(
                "File",
                {"content_doctype": "Presentation", "content_docname": presentation.name},
                "name",
            )
            if not file:
                file = create_drive_file(
                    presentation.title or "Untitled",
                    get_user_folder(presentation.owner).name,
                    "Presentation",
                    None,
                    mime_type="frappe/slides",
                    content_doctype="Presentation",
                    content_docname=presentation.name,
                    owner=presentation.owner,
                ).name

            if presentation.name in public and not frappe.db.exists(
                "Drive Permission", {"entity": file, "user": ""}
            ):
                frappe.get_doc({"doctype": "Drive Permission", "entity": file, "user": "", "read": 1}).insert(
                    ignore_permissions=True
                )
    finally:
        frappe.flags.mute_drive_activity_log = False
