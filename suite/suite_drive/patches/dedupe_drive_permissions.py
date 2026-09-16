import frappe


def execute():
    """One row per (entity, user); resolution assumes it when ranking by
    specificity. Keep the most permissive of any duplicates, preferring a deny."""
    duplicates = frappe.db.sql(
        """SELECT entity, user FROM `tabDrive Permission`
		GROUP BY entity, user HAVING COUNT(*) > 1""",
        as_dict=True,
    )
    for row in duplicates:
        names = frappe.get_all(
            "Drive Permission",
            filters={"entity": row.entity, "user": row.user},
            fields=["name", "deny", "read", "comment", "share", "upload", "write"],
            order_by="deny desc, creation",
        )
        keep, rest = names[0], names[1:]
        for other in rest:
            if other.deny == keep.deny:
                for t in ("read", "comment", "share", "upload", "write"):
                    if other.get(t):
                        frappe.db.set_value("Drive Permission", keep.name, t, 1, update_modified=False)
            frappe.delete_doc("Drive Permission", other.name, ignore_permissions=True, force=True)

    if not frappe.db.sql("""SHOW INDEX FROM `tabDrive Permission` WHERE Key_name = 'unique_entity_user'"""):
        frappe.db.sql_ddl(
            """ALTER TABLE `tabDrive Permission`
			ADD UNIQUE INDEX `unique_entity_user` (`entity`, `user`)"""
        )
