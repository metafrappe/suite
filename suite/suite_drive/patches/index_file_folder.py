import frappe


def execute():
    """Every Drive listing and tree walk filters on `File.folder`, which core leaves
    unindexed. `team` is indexed only for the collapse's per-team lookup;
    drop_team_doctypes discards it with the column."""
    for column in ("folder", "team"):
        if not frappe.db.has_column("File", column):
            continue
        if frappe.db.sql("show index from `tabFile` where Column_name = %s", column):
            continue
        frappe.db.sql_ddl(f"ALTER TABLE `tabFile` ADD INDEX `{column}` (`{column}`)")
