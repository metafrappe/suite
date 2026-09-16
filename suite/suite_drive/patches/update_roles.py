import frappe


def execute():
    if not frappe.db.table_exists("Drive Team Member"):
        # Site never had the legacy Drive schema — nothing to migrate.
        return

    frappe.reload_doc("Suite Drive", "doctype", "Drive Team Member")
    for id in frappe.get_all("Drive Team Member"):
        member = frappe.get_doc("Drive Team Member", id)
        member.access_level = 2 if member.is_admin else 1
        member.save()
