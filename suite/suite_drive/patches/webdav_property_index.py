import frappe


def execute():
    """One row per (entity, ns, prop_name) — PROPPATCH upserts assume it."""
    if not frappe.db.table_exists("Drive DAV Property"):
        return
    # idempotent: checks information_schema before issuing the DDL
    frappe.db.add_unique(
        "Drive DAV Property", ["entity", "ns", "prop_name"], constraint_name="unique_entity_prop"
    )
