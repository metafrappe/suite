"""Live WebDAV properties and the ETag scheme.

ETags are strong: PUT/COPY populate File.content_hash (unused by Drive) with
the body's SHA-256; legacy rows fall back to name+size+mtime, which is
byte-stable because every content mutation either writes content_hash or
creates a new entity.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import frappe
from lxml import etree
from werkzeug.http import http_date

from suite.suite_drive.webdav.xmlutil import dav, dav_element


def compute_etag(row: frappe._dict) -> str:
    if row.get("content_hash"):
        # 128 bits of the hash: plenty for cache validation, and short enough
        # for clients with tight header buffers (litmus builds If into 200 bytes)
        return f'"sha256-{row.content_hash[:32]}"'
    stamp = _as_datetime(row.modified).strftime("%Y%m%d%H%M%S%f")
    return f'"{row.name}-{row.file_size or 0}-{stamp}"'


def rfc1123(value: datetime | str) -> str:
    return http_date(_to_utc(value))


def modified_utc(row: frappe._dict) -> datetime:
    return _to_utc(row.modified)


def to_site_naive(value: datetime) -> datetime:
    """Aware datetime -> the naive site-local form the DB stores."""
    return value.astimezone(_site_zone()).replace(tzinfo=None)


def iso8601(value: datetime | str) -> str:
    return _to_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def live_properties(
    row: frappe._dict | None,
    *,
    is_collection: bool,
    display_name: str,
    quota: tuple[int, int] | None = None,
) -> dict[str, etree._Element | None]:
    """All live properties for one resource, keyed by Clark name; None = not
    defined for this resource (rendered as a 404 propstat when requested).

    quota = (used_bytes, limit_bytes); limit 0 means unlimited (RFC 4331 allows
    omitting quota-available-bytes then). lockdiscovery/supportedlock are
    contributed by the locking module at assembly time.
    """
    props: dict[str, etree._Element | None] = {
        dav("displayname"): dav_element("displayname", text=display_name),
        dav("resourcetype"): _resourcetype(is_collection),
        dav("getcontentlength"): None,
        dav("getcontenttype"): None,
        dav("getetag"): None,
        dav("getlastmodified"): None,
        dav("creationdate"): None,
        dav("quota-used-bytes"): None,
        dav("quota-available-bytes"): None,
    }

    if row is not None:
        props[dav("getlastmodified")] = dav_element("getlastmodified", text=rfc1123(row.modified))
        props[dav("creationdate")] = dav_element("creationdate", text=iso8601(row.creation))

    if row is not None and not is_collection:
        # folders carry rolled-up subtree sizes; clients misrender them as content-length
        props[dav("getcontentlength")] = dav_element("getcontentlength", text=str(row.file_size or 0))
        props[dav("getcontenttype")] = dav_element(
            "getcontenttype", text=row.mime_type or "application/octet-stream"
        )
        props[dav("getetag")] = dav_element("getetag", text=compute_etag(row))

    if is_collection and quota is not None:
        used, limit = quota
        props[dav("quota-used-bytes")] = dav_element("quota-used-bytes", text=str(used))
        if limit:
            props[dav("quota-available-bytes")] = dav_element(
                "quota-available-bytes", text=str(max(0, limit - used))
            )

    return props


def _resourcetype(is_collection: bool) -> etree._Element:
    element = dav_element("resourcetype")
    if is_collection:
        etree.SubElement(element, dav("collection"))
    return element


def _to_utc(value: datetime | str) -> datetime:
    # naive site-local stamps are ambiguous during the DST fall-back hour;
    # fold=0 picks the earlier instant, the best the lost offset allows
    return _as_datetime(value).replace(tzinfo=_site_zone()).astimezone(UTC)


def _as_datetime(value: datetime | str) -> datetime:
    if isinstance(value, str):
        return frappe.utils.get_datetime(value)
    return value


def _site_zone() -> ZoneInfo:
    return ZoneInfo(frappe.utils.get_system_timezone())
