from collections import defaultdict

import frappe
from frappe.utils import now

# (old Drive role -> shared Suite role) that access has been consolidated onto.
ROLE_MAP = {
    "Drive User": "Suite User",
    "Drive Admin": "Suite Admin",
}


def execute() -> None:
    """Move existing Drive role assignments onto the shared Suite roles.

    Drive access moved from the app-specific "Drive User"/"Drive Admin" roles to
    the suite-wide "Suite User"/"Suite Admin" roles. Both Drive roles carried desk
    access, as do the Suite roles now, so holders stay System Users and their
    ``user_type`` needs no recompute — the assignments are simply swapped. Users
    currently holding a Drive role gain the matching Suite role (if they don't have
    it already), then the stale Drive assignments are dropped. The Drive Role docs
    themselves are left in place; deleting a role cascades through core.
    """

    for suite_role in set(ROLE_MAP.values()):
        if not frappe.db.exists("Role", suite_role):
            frappe.get_doc({"doctype": "Role", "role_name": suite_role, "desk_access": 1}).insert(
                ignore_permissions=True
            )

    users_by_drive_role = {}
    for drive_role in ROLE_MAP:
        users = set(
            frappe.get_all(
                "Has Role",
                filters={"parenttype": "User", "role": drive_role},
                pluck="parent",
            )
        )
        users.discard("Administrator")
        users_by_drive_role[drive_role] = users

    _grant_suite_roles(users_by_drive_role)

    frappe.db.delete("Has Role", {"parenttype": "User", "role": ("in", list(ROLE_MAP.keys()))})


def _grant_suite_roles(users_by_drive_role: dict[str, set[str]]) -> None:
    """Bulk-insert the matching Suite role for each Drive-role holder.

    The Suite roles carry desk access, but every user being migrated already holds
    a desk-access Drive role and is therefore already a System User, so inserting
    the child rows directly (rather than via ``User.add_roles``, an N+1 on migrate)
    does not leave ``user_type`` stale.

    Each user's existing roles and max ``idx`` are read once, up front, and the
    per-user ``idx`` is then bumped locally as rows are appended. Doing it in a
    single pass — rather than re-querying per role — means a user who holds both
    Drive roles gets consecutive ``idx`` values across the two grants instead of a
    collision, without depending on the just-inserted rows being visible mid-loop.
    """

    all_users = set().union(*users_by_drive_role.values())
    if not all_users:
        return

    existing = frappe.get_all(
        "Has Role",
        filters={"parenttype": "User", "parent": ("in", list(all_users))},
        fields=["parent", "role", "idx"],
    )
    roles_by_user = defaultdict(set)
    next_idx = defaultdict(int)
    for row in existing:
        roles_by_user[row.parent].add(row.role)
        next_idx[row.parent] = max(next_idx[row.parent], row.idx)

    timestamp = now()
    rows = []
    for drive_role, suite_role in ROLE_MAP.items():
        for user in users_by_drive_role[drive_role]:
            if suite_role in roles_by_user[user]:
                continue
            roles_by_user[user].add(suite_role)
            next_idx[user] += 1
            rows.append(
                (
                    frappe.generate_hash(length=10),
                    user,
                    "User",
                    "roles",
                    suite_role,
                    "Administrator",
                    timestamp,
                    timestamp,
                    "Administrator",
                    next_idx[user],
                    0,
                )
            )

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
