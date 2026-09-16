import frappe


def execute():
    """Drop the Drive Team doctypes and every leftover team column, after
    remove_teams has restructured the tree."""
    frappe.delete_doc_if_exists("Custom Field", "File-team")
    frappe.delete_doc_if_exists("DocType", "Drive Team Member")
    frappe.delete_doc_if_exists("DocType", "Drive Team")
    # deleting a DocType leaves its table behind
    frappe.db.sql_ddl("DROP TABLE IF EXISTS `tabDrive Team Member`")
    frappe.db.sql_ddl("DROP TABLE IF EXISTS `tabDrive Team`")

    for table, column in [
        ("File", "team"),
        ("Drive Permission", "team"),
        ("Drive User Invitation", "team"),
        ("Drive User Invitation", "as_guest"),
        ("Writer Template", "team"),
    ]:
        if frappe.db.has_column(table, column):
            frappe.db.sql_ddl(f"ALTER TABLE `tab{table}` DROP COLUMN `{column}`")
