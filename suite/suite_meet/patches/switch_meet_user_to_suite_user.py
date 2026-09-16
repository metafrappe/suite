import frappe
from frappe.utils import now

OLD_ROLE = "Meet User"
NEW_ROLE = "Suite User"


def execute() -> None:
    """Move existing "Meet User" assignments onto the suite-wide "Suite User" role.

    Meet access moved from the app-specific "Meet User" role to the shared
    "Suite User" role. Users currently holding "Meet User" must gain "Suite User"
    (if they don't have it already) so they keep access on upgrade, then the stale
    "Meet User" assignments are dropped. The "Meet User" Role doc itself is left in
    place — deleting a role cascades through core and is not worth the risk here.
    """

    if not frappe.db.exists("Role", NEW_ROLE):
        frappe.get_doc({"doctype": "Role", "role_name": NEW_ROLE, "desk_access": 1}).insert(
            ignore_permissions=True
        )

    meet_users = set(
        frappe.get_all(
            "Has Role",
            filters={"parenttype": "User", "role": OLD_ROLE},
            pluck="parent",
        )
    )
    meet_users.discard("Administrator")

    _grant_suite_role(meet_users)

    frappe.db.delete("Has Role", {"parenttype": "User", "role": OLD_ROLE})


def _grant_suite_role(users: set[str]) -> None:
    """Grant NEW_ROLE to ``users`` with one bulk insert into ``Has Role``.

    NEW_ROLE carries desk access, and the bulk insert does not recompute
    ``user_type``; that is fine because every enabled user already holds a
    desk-access role (Drive/Suite) and is therefore already a System User.
    Inserting the child rows directly avoids the N+1 of ``User.add_roles`` on
    migrate. Users who already hold the role are skipped, and the next ``idx``
    per user is derived from their existing rows.
    """

    if not users:
        return

    existing = frappe.get_all(
        "Has Role",
        filters={"parenttype": "User", "parent": ("in", list(users))},
        fields=["parent", "role", "idx"],
    )
    already_has = {row.parent for row in existing if row.role == NEW_ROLE}
    next_idx = {}
    for row in existing:
        next_idx[row.parent] = max(next_idx.get(row.parent, 0), row.idx)

    timestamp = now()
    rows = [
        (
            frappe.generate_hash(length=10),
            user,
            "User",
            "roles",
            NEW_ROLE,
            "Administrator",
            timestamp,
            timestamp,
            "Administrator",
            next_idx.get(user, 0) + 1,
            0,
        )
        for user in users
        if user not in already_has
    ]
    if not rows:
        return

    frappe.db.bulk_insert(
        "Has Role",
        fields=[
            "name",
            "parent",
            "parenttype",
            "parentfield",
            "role",
            "owner",
            "creation",
            "modified",
            "modified_by",
            "idx",
            "docstatus",
        ],
        values=rows,
    )
