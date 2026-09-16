import os

import frappe
import requests
from frappe.rate_limiter import rate_limit
from frappe.utils import now


def mark_as_viewed(entity):
    if (
        frappe.session.user == "Guest"
        or not frappe.has_permission(doctype="Drive Entity Log", ptype="create", user=frappe.session.user)
        or entity.is_folder
    ):
        return

    entity_log = frappe.db.get_value(
        "Drive Entity Log", {"entity_name": entity.name, "user": frappe.session.user}
    )
    if entity_log:
        frappe.db.set_value(
            "Drive Entity Log",
            entity_log,
            "last_interaction",
            now(),
            update_modified=False,
        )
        return
    doc = frappe.new_doc("Drive Entity Log")
    doc.entity_name = entity.name
    doc.user = frappe.session.user
    doc.last_interaction = now()
    doc.insert()
    return doc


def get_country_info():
    ip = frappe.local.request_ip

    def _get_country_info():
        fields = [
            "status",
            "message",
            "continent",
            "continentCode",
            "country",
            "countryCode",
            "region",
            "regionName",
            "city",
            "district",
            "zip",
            "lat",
            "lon",
            "timezone",
            "offset",
            "currency",
            "isp",
            "org",
            "as",
            "asname",
            "reverse",
            "mobile",
            "proxy",
            "hosting",
            "query",
        ]

        try:
            res = requests.get(f"https://pro.ip-api.com/json/{ip}?fields={','.join(fields)}")
            data = res.json()
            if data.get("status") != "fail":
                return data
        except Exception:
            pass

        return {}

    return frappe.cache().hget("ip_country_map", ip, generator=_get_country_info)


def create_drive_settings(user, method: str | None = None) -> None:
    """Create Drive Settings and the private user folder for a newly created User."""
    from suite.suite_drive.utils import get_user_folder

    if user.flags.get("skip_drive_setup"):
        return

    if not user.name or user.name in ("Guest", "Administrator"):
        return

    get_user_folder(user.name)
