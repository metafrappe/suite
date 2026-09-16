import frappe

from suite.suite_drive.api.permissions import user_has_permission
from suite.suite_drive.overrides.file import File, content_query_conditions

READ_PTYPES = frozenset({"read", "report", "export", "email", "print", "select"})


def filter_templates(user):
    # Templates are site-wide readable; guests get nothing.
    if (user or frappe.session.user) == "Guest":
        return "1=0"
    return ""


def template_has_permission(doc, ptype="read", user=None):
    user = user or frappe.session.user
    if ptype == "create" or user == "Administrator":
        return True
    if ptype in READ_PTYPES:
        return user != "Guest"
    return doc.get("owner") == user


def document_query_conditions(user):
    """`permission_query_conditions` for Writer Document — delegate to the
    content SDK so list views can't enumerate documents the caller can't read."""
    return content_query_conditions("Writer Document", user)


def version_has_permission(doc, ptype="read", user=None):
    """A Writer Version is readable/writable iff the backing Drive File of its
    parent document is."""
    user = user or frappe.session.user
    if user == "Administrator":
        return True
    parent = doc.get("doc")
    if not parent:
        return False
    file = File.get_for_doc("Writer Document", parent)
    if not file:
        return False
    return bool(user_has_permission(file, "read" if ptype in READ_PTYPES else "write", user))


def version_query_conditions(user):
    """`permission_query_conditions` for Writer Version — scope rows to versions
    whose parent document the caller can read (owned or directly shared)."""
    if user == "Administrator":
        return ""
    doc_predicate = content_query_conditions("Writer Document", user)
    if not doc_predicate:
        return ""
    return (
        "`tabWriter Version`.doc IN ("
        "SELECT `tabWriter Document`.name FROM `tabWriter Document` "
        f"WHERE {doc_predicate})"
    )
