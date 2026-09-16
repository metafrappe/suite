import frappe


def execute():
    if not frappe.db.table_exists("Drive Team Member"):
        # Site never had the legacy Drive schema — nothing to migrate.
        return

    for user in frappe.db.get_list("User", pluck="name"):
        teams = frappe.get_all(
            "Drive Team Member",
            pluck="parent",
            filters=[
                ["parenttype", "=", "Drive Team"],
                ["user", "=", user],
            ],
        )
        if teams:
            if not frappe.db.exists("Drive Settings", {"user": user}):
                frappe.get_doc(
                    {
                        "doctype": "Drive Settings",
                        "user": user,
                        "single_click": 1,
                        "default_team": teams[0],
                    }
                ).insert()
