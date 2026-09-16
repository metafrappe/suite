import frappe


def execute():
    if not frappe.db.table_exists("Drive File"):
        # Site never had the legacy Drive schema — nothing to migrate.
        return

    for k in frappe.get_all("Drive File", fields=["name", "modified"]):
        frappe.db.set_value(
            "Drive File",
            k.name,
            "_modified",
            k.modified.strftime("%Y-%m-%d %H:%M:%S.%f"),
            update_modified=False,
        )
