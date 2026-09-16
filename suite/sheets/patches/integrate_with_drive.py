import frappe

from suite.suite_drive.utils import STATUS_TRASHED, create_drive_file, get_new_file_name, get_user_folder


def execute():
    """Back existing sheets with Drive Files so they show up in Drive, the same
    way sheets created after this change are auto-backed in Sheet.after_insert.

    Only existence/listing is mirrored — sharing stays on the sheet's own
    DocShare model. Idempotent: sheets that already have a backing File are
    skipped, so a re-run (or a sheet created between deploy and patch) is safe.
    """
    frappe.flags.mute_drive_activity_log = True
    try:
        sheets = frappe.get_all("Sheet", fields=["name", "title", "owner", "trashed"])
        for sheet in sheets:
            if frappe.db.get_value(
                "File", {"content_doctype": "Sheet", "content_docname": sheet.name}, "name"
            ):
                continue

            home = get_user_folder(sheet.owner).name
            file = create_drive_file(
                # Dedupe within the user folder so backfilling many like-titled
                # sheets (e.g. several "Untitled Spreadsheet") doesn't create
                # ambiguous same-named Drive files.
                get_new_file_name(sheet.title or "Untitled Spreadsheet", home, "Spreadsheet"),
                home,
                "Spreadsheet",
                None,
                mime_type="frappe/sheet",
                content_doctype="Sheet",
                content_docname=sheet.name,
                owner=sheet.owner,
            )
            # A trashed sheet's File must land in the trash too, not the listing.
            if sheet.trashed:
                frappe.db.set_value("File", file.name, "status", STATUS_TRASHED, update_modified=False)
    finally:
        frappe.flags.mute_drive_activity_log = False
