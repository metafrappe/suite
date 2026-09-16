"""URL path <-> File entity resolution and the WebDAV naming policy.

The DAV namespace shows two mounts — Home (the user's folder) and Everyone
(the shared `Drive` root). Below them the folder adjacency list is walked one
indexed point query per segment: exact (BINARY) match first, one
case-insensitive fallback when unambiguous, oldest row winning for exact
duplicates. Entities WebDAV cannot represent (content-doc-backed files, links,
names containing separators) are invisible.
"""

from dataclasses import dataclass, field
from urllib.parse import quote, unquote, urlsplit

import frappe
from werkzeug.wrappers import Request

from suite.suite_drive.utils import ROOT_FOLDER, get_root_folder, get_user_folder
from suite.suite_drive.webdav import DAV_PREFIX
from suite.suite_drive.webdav.context import validate_segments
from suite.suite_drive.webdav.errors import BadGateway, BadRequest, Forbidden

HOME_ALIAS = "Home"
EVERYONE_ALIAS = "Everyone"

MAX_NAME_LENGTH = 140

_PROJECTION = """
    `name`, file_name, folder, is_folder, file_size, file_url, mime_type, file_type,
    `owner`, creation, COALESCE(file_modified, modified) AS modified,
    content_hash, content_doctype, content_docname, attached_to_doctype, attached_to_name
"""

# rows WebDAV can serve: real bytes (or folders), not content-doc mirrors or links
_REPRESENTABLE = "content_doctype IS NULL AND (file_type IS NULL OR file_type != 'Link')"


@dataclass
class ResolvedPath:
    segments: list[str] = field(default_factory=list)  # decoded, mount alias included
    root: str = "virtual"  # "virtual" | "home" | "everyone" | "unknown"
    entity: frappe._dict | None = None  # leaf row (the mount row when is_mount)
    parent: frappe._dict | None = None  # parent row when only the leaf is missing
    missing_intermediate: bool = False
    is_mount: bool = False

    @property
    def exists(self) -> bool:
        return self.entity is not None

    @property
    def is_collection(self) -> bool:
        return self.is_mount or bool(self.entity and self.entity.is_folder)


def resolve(segments: list[str], user: str) -> ResolvedPath:
    if not segments:
        return ResolvedPath(segments=[], is_mount=True)

    alias = segments[0].lower()
    if alias == HOME_ALIAS.lower():
        root, mount_name = "home", get_user_folder(user).name
    elif alias == EVERYONE_ALIAS.lower():
        root, mount_name = "everyone", get_root_folder().name
    else:
        return ResolvedPath(segments=segments, root="unknown", missing_intermediate=len(segments) > 1)

    current = _fetch(mount_name)
    if len(segments) == 1:
        return ResolvedPath(segments=segments, root=root, entity=current, is_mount=True)

    for segment in segments[1:-1]:
        current = _child(current.name, segment)
        if current is None or not current.is_folder:
            return ResolvedPath(segments=segments, root=root, missing_intermediate=True)

    leaf = _child(current.name, segments[-1])
    return ResolvedPath(segments=segments, root=root, entity=leaf, parent=current)


def list_children(parent_name: str) -> list[frappe._dict]:
    """All representable, addressable children — oldest row wins exact-name duplicates."""
    # CHAR(92) is the backslash: spelled as a LIKE pattern it is unescaped
    # twice (string literal, then LIKE) and ends up matching '%' instead
    rows = frappe.db.sql(
        f"""SELECT {_PROJECTION}
        FROM `tabFile`
        WHERE folder = %(parent)s AND status = 'Active' AND {_REPRESENTABLE}
            AND file_name NOT LIKE '%%/%%' AND INSTR(file_name, CHAR(92)) = 0
            AND file_name NOT IN ('.', '..')
        ORDER BY creation ASC""",
        values={"parent": parent_name},
        as_dict=True,
    )
    seen: set[str] = set()
    children = []
    for row in rows:
        if row.file_name in seen:
            continue
        seen.add(row.file_name)
        children.append(row)
    return children


def validate_dav_name(name: str, parent: frappe._dict) -> None:
    """The naming policy WebDAV enforces on create (Drive itself is laxer)."""
    if not name or len(name) > MAX_NAME_LENGTH:
        raise BadRequest(f"Name must be between 1 and {MAX_NAME_LENGTH} characters.")
    if "/" in name or "\\" in name or any(ord(c) < 0x20 for c in name) or name in (".", ".."):
        raise BadRequest("Name contains unsupported characters.")
    if name.lower() in _reserved_names(parent):
        raise Forbidden(f'The name "{name}" is reserved.')


def parse_destination(request: Request) -> tuple[list[str], bool]:
    """Decode the Destination header into path segments below /dav."""
    header = request.headers.get("Destination")
    if not header:
        raise BadRequest("Destination header is required.")

    parts = urlsplit(header)
    if parts.netloc and not _same_host(parts.netloc, request.host):
        raise BadGateway("Destination is on another host.")
    raw_path = parts.path
    if raw_path != DAV_PREFIX and not raw_path.startswith(DAV_PREFIX + "/"):
        raise BadGateway("Destination is outside the WebDAV namespace.")

    try:
        segments = [
            unquote(segment, errors="strict") for segment in raw_path[len(DAV_PREFIX) :].split("/") if segment
        ]
    except UnicodeDecodeError as e:
        raise BadRequest("Malformed Destination header.") from e
    return validate_segments(segments), raw_path.endswith("/")


def href_for(segments: list[str], is_collection: bool) -> str:
    href = DAV_PREFIX + "/" + "/".join(quote(segment, safe="") for segment in segments)
    if is_collection and not href.endswith("/"):
        href += "/"
    return href if segments else DAV_PREFIX + "/"


def fetch(name: str) -> frappe._dict | None:
    return _fetch(name)


def reset_memo() -> None:
    """Per-request memo; only long-lived contexts (tests, console) need to reset it."""
    frappe.local._webdav_path_memo = {}


def _fetch(name: str) -> frappe._dict | None:
    rows = frappe.db.sql(
        f"SELECT {_PROJECTION} FROM `tabFile` WHERE `name` = %(name)s LIMIT 1",
        values={"name": name},
        as_dict=True,
    )
    return rows[0] if rows else None


def _child(parent_name: str, segment: str) -> frappe._dict | None:
    memo = getattr(frappe.local, "_webdav_path_memo", None)
    if memo is None:
        memo = frappe.local._webdav_path_memo = {}
    key = (parent_name, segment)
    if key in memo:
        return memo[key]

    base = f"""SELECT {_PROJECTION} FROM `tabFile`
        WHERE folder = %(parent)s AND status = 'Active' AND {_REPRESENTABLE}"""
    values = {"parent": parent_name, "segment": segment}

    rows = frappe.db.sql(
        base + " AND file_name = BINARY %(segment)s ORDER BY creation ASC LIMIT 1",
        values=values,
        as_dict=True,
    )
    if not rows:
        # tolerate case-sloppy clients, but only when unambiguous
        rows = frappe.db.sql(
            base + " AND file_name = %(segment)s ORDER BY creation ASC LIMIT 2",
            values=values,
            as_dict=True,
        )
        if len(rows) != 1:
            rows = []

    memo[key] = rows[0] if rows else None
    return memo[key]


def _reserved_names(parent: frappe._dict) -> set[str]:
    reserved = {".embeds"}
    if parent and parent.get("name") == ROOT_FOLDER:
        settings = frappe.get_cached_doc("Drive Disk Settings")
        reserved |= {
            ".trash",
            ".uploads",
            ".drive-downloads",
            "users",
            (settings.thumbnail_prefix or ".thumbnails").lower(),
        }
    return reserved


def _same_host(destination: str, request_host: str) -> bool:
    """Hostnames must match; ports only when both sides state a non-default
    one — a proxy rewriting Host with nginx's $host drops the port, which
    must not fail every MOVE/COPY on a non-standard port."""
    try:
        dest, req = urlsplit("//" + destination), urlsplit("//" + request_host)
        ports = {port for port in (dest.port, req.port) if port not in (None, 80, 443)}
    except ValueError:
        return False
    return bool(dest.hostname) and dest.hostname == req.hostname and len(ports) <= 1
