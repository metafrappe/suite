import frappe

from suite.utils.user import is_administrator, is_suite_user


def has_app_permission() -> bool:
    user = frappe.session.user
    return is_administrator(user) or is_suite_user(user)
