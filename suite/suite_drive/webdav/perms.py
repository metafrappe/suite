"""Batched permission resolution for directory listings.

Drive's own listings resolve one recursive CTE per row (api/list.py). A
PROPFIND Depth:1 instead runs the parent's CTE once, fetches the children's own
Drive Permission rows in one query, and composes each child in Python with the
exact nearest-wins semantics of dribble_access: a child's own rows decide
first, then the state inherited at the parent, then deny.
"""

import frappe

from suite.suite_drive.api.permissions import get_user_access_for_user, is_drive_admin
from suite.suite_drive.utils import (
    GENERAL_USER,
    GROUP_PREFIX,
    PERMISSION_TYPES,
    generate_upward_path,
    get_principals,
)

ALL_ACCESS = dict.fromkeys(PERMISSION_TYPES, 1)
NO_ACCESS = dict.fromkeys(PERMISSION_TYPES, 0)


def resolve_entity_access(entity: frappe._dict, user: str) -> dict:
    """Depth:0 access for one already-fetched row (admin/owner/attachment included)."""
    return get_user_access_for_user(entity, user)


def resolve_children_access(
    parent_path: list[dict], children: list[frappe._dict], user: str
) -> dict[str, dict]:
    """Access bits per child name. parent_path is generate_upward_path(parent, user)."""
    if is_drive_admin(user):
        return {child.name: dict(ALL_ACCESS) for child in children}

    inherited = _dribble_state(parent_path)
    own_rows = _child_permission_rows([child.name for child in children], user)

    access = {}
    for child in children:
        if user != "Guest" and child.owner == user:
            # ownership bypasses any deny on the path
            access[child.name] = dict(ALL_ACCESS)
        elif child.get("attached_to_doctype") and child.get("attached_to_name"):
            # rare: adopted framework attachments delegate to their reference doc
            access[child.name] = get_user_access_for_user(child, user)
        else:
            access[child.name] = _compose(own_rows.get(child.name, []), inherited)
    return access


def _compose(child_rows: list[dict], inherited: dict[str, int]) -> dict:
    # identical evaluation order to dribble_access over parent_path + [child]:
    # the child's own rows are nearest and decide first, then the parent state
    decided = {}
    for row in child_rows:
        for ptype in PERMISSION_TYPES:
            if row[ptype] and ptype not in decided:
                decided[ptype] = 0 if row["deny"] else 1
    return {ptype: decided.get(ptype, inherited.get(ptype, 0)) for ptype in PERMISSION_TYPES}


def _dribble_state(path: list[dict]) -> dict[str, int]:
    """dribble_access without the default-deny: undecided types stay absent."""
    decided = {}
    for node in path[::-1]:
        for row in node.get("perms", ()):
            for ptype in PERMISSION_TYPES:
                if row[ptype] and ptype not in decided:
                    decided[ptype] = 0 if row["deny"] else 1
    return decided


def _child_permission_rows(child_names: list[str], user: str) -> dict[str, list[dict]]:
    if not child_names:
        return {}
    principals = get_principals(user)
    rows = frappe.get_all(
        "Drive Permission",
        filters={"entity": ["in", child_names], "user": ["in", principals]},
        fields=["entity", "user", "deny", *PERMISSION_TYPES],
    )

    # same ordering the upward-path CTE applies within one node:
    # specificity tier, then deny before grant within a tier
    def tier(row):
        if row.user == user:
            return 0
        if row.user.startswith(GROUP_PREFIX):
            return 1
        if row.user == GENERAL_USER:
            return 2
        return 3

    grouped: dict[str, list[dict]] = {}
    for row in sorted(rows, key=lambda row: (tier(row), -row.deny)):
        grouped.setdefault(row.entity, []).append(row)
    return grouped


def parent_access(parent_path: list[dict], parent: frappe._dict, user: str) -> dict:
    """Access at the path's leaf, with the owner/admin short-circuits applied."""
    if is_drive_admin(user):
        return dict(ALL_ACCESS)
    if user != "Guest" and parent.owner == user:
        return dict(ALL_ACCESS)
    leaf = parent_path[-1]
    return {ptype: leaf.get(ptype, 0) for ptype in PERMISSION_TYPES}
