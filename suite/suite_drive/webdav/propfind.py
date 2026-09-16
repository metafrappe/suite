"""PROPFIND: Depth 0/1 property listings as 207 multistatus.

Depth infinity is refused with the RFC 4918 §9.1 propfind-finite-depth
precondition (Apache's default too) — an unbounded walk over the adjacency
list with permission fanout is a DoS vector, and no target client uses it.
"""

from dataclasses import dataclass

import frappe
from lxml import etree
from werkzeug.wrappers import Response

from suite.suite_drive.utils import ROOT_FOLDER, generate_upward_path, get_user_folder
from suite.suite_drive.webdav import pathmap, perms
from suite.suite_drive.webdav.context import DavContext
from suite.suite_drive.webdav.errors import BadRequest, Forbidden, NotFoundError
from suite.suite_drive.webdav.properties import live_properties
from suite.suite_drive.webdav.xmlutil import XML_BODY_CAP, MultistatusBuilder, dav, parse_xml

VIRTUAL_ROOT_NAME = "Frappe Drive"
# RFC 4331 §2: quota properties SHOULD NOT be returned on allprop
QUOTA_PROPS = frozenset({dav("quota-used-bytes"), dav("quota-available-bytes")})


@dataclass
class Resource:
    row: frappe._dict | None  # None only for the virtual root
    segments: list[str]
    is_collection: bool
    display_name: str
    ancestors: list[str] | None = None  # for lockdiscovery inheritance


def handle(ctx: DavContext) -> Response:
    depth = ctx.depth if ctx.depth is not None else "infinity"
    if depth == "infinity":
        raise Forbidden(
            "PROPFIND with Depth: infinity is not supported.",
            condition="propfind-finite-depth",
        )

    mode, requested = _parse_body(ctx)
    resources = _collect_resources(ctx, depth)
    quota = _current_quota(ctx.user) if mode == "prop" and QUOTA_PROPS & set(requested) else None

    from suite.suite_drive.webdav import deadprops, locks

    dead = deadprops.get_dead_props([r.row.name for r in resources if r.row is not None])
    lock_map = locks.discovery_map({r.row.name: r.ancestors or [] for r in resources if r.row is not None})

    builder = MultistatusBuilder()
    for resource in resources:
        dead_props = dead.get(resource.row.name, {}) if resource.row is not None else {}
        row_locks = lock_map.get(resource.row.name, []) if resource.row is not None else []
        _render(builder, resource, mode, requested, quota, dead_props, row_locks, ctx.user)
    return builder.build()


def _parse_body(ctx: DavContext) -> tuple[str, list[str]]:
    root = parse_xml(ctx.body.read_all(XML_BODY_CAP))
    if root is None:
        return "allprop", []
    if root.tag != dav("propfind"):
        raise BadRequest("Expected a DAV:propfind request body.")

    if root.find(dav("propname")) is not None:
        return "propname", []
    prop = root.find(dav("prop"))
    if prop is not None:
        return "prop", [child.tag for child in prop if isinstance(child.tag, str)]
    if root.find(dav("allprop")) is not None:
        include = root.find(dav("include"))
        extra = [child.tag for child in include if isinstance(child.tag, str)] if include is not None else []
        return "allprop", extra
    raise BadRequest("Empty DAV:propfind request body.")


def _collect_resources(ctx: DavContext, depth: str) -> list[Resource]:
    resolved = pathmap.resolve(ctx.segments, ctx.user)

    if resolved.root == "virtual" and resolved.is_mount:
        return _virtual_root_resources(ctx, depth)

    if not resolved.exists:
        raise NotFoundError("Resource not found.")

    row = resolved.entity
    display = _display_name(resolved)
    parent_path = generate_upward_path(row.name, ctx.user)
    if row.get("attached_to_doctype"):
        access = perms.resolve_entity_access(row, ctx.user)
    else:
        access = perms.parent_access(parent_path, row, ctx.user)
    if not access["read"]:
        # indistinguishable from absent, matching Drive's anti-enumeration stance
        raise NotFoundError("Resource not found.")

    target_ancestors = [node["name"] for node in parent_path[:-1]]
    resources = [Resource(row, list(ctx.segments), resolved.is_collection, display, target_ancestors)]
    if depth == "1" and resolved.is_collection:
        child_ancestors = [*target_ancestors, row.name]
        children = pathmap.list_children(row.name)
        child_access = perms.resolve_children_access(parent_path, children, ctx.user)
        for child in children:
            if not child_access[child.name]["read"]:
                continue
            resources.append(
                Resource(
                    child,
                    [*ctx.segments, child.file_name],
                    bool(child.is_folder),
                    child.file_name,
                    child_ancestors,
                )
            )
    return resources


def _virtual_root_resources(ctx: DavContext, depth: str) -> list[Resource]:
    resources = [Resource(None, [], True, VIRTUAL_ROOT_NAME)]
    if depth != "1":
        return resources

    home = pathmap.fetch(get_user_folder(ctx.user).name)
    resources.append(Resource(home, [pathmap.HOME_ALIAS], True, pathmap.HOME_ALIAS))

    everyone = pathmap.fetch(ROOT_FOLDER)
    if everyone is not None and perms.resolve_entity_access(everyone, ctx.user)["read"]:
        resources.append(Resource(everyone, [pathmap.EVERYONE_ALIAS], True, pathmap.EVERYONE_ALIAS))
    return resources


def _display_name(resolved: pathmap.ResolvedPath) -> str:
    if resolved.is_mount:
        return pathmap.HOME_ALIAS if resolved.root == "home" else pathmap.EVERYONE_ALIAS
    return resolved.entity.file_name


def _render(
    builder: MultistatusBuilder,
    resource: Resource,
    mode: str,
    requested: list[str],
    quota: tuple[int, int] | None,
    dead_props: dict[str, etree._Element],
    row_locks: list,
    viewer: str,
) -> None:
    from suite.suite_drive.webdav import locks

    available = live_properties(
        resource.row,
        is_collection=resource.is_collection,
        display_name=resource.display_name,
        quota=quota if resource.is_collection else None,
    )
    available[dav("supportedlock")] = locks.supportedlock_xml()
    available[dav("lockdiscovery")] = locks.lockdiscovery_xml(row_locks, viewer)
    response = builder.add_response(pathmap.href_for(resource.segments, resource.is_collection))

    if mode == "propname":
        names = [tag for tag, value in available.items() if value is not None]
        names += list(dead_props)
        response.propstat(200, [etree.Element(tag) for tag in names])
        return

    if mode == "allprop":
        found = [value for tag, value in available.items() if value is not None and tag not in QUOTA_PROPS]
        found += [
            available[tag] for tag in requested if available.get(tag) is not None and tag in QUOTA_PROPS
        ]
        found += list(dead_props.values())
        response.propstat(200, found)
        return

    found, missing = [], []
    for tag in requested:
        value = available.get(tag)
        if value is None:
            value = dead_props.get(tag)
        if value is not None:
            found.append(value)
        else:
            missing.append(etree.Element(tag))
    response.propstat(200, found)
    response.propstat(404, missing)


def _current_quota(user: str) -> tuple[int, int]:
    from suite.suite_drive.api.storage import get_quota, get_storage_usage

    return get_storage_usage(user)["total_size"], get_quota(user)
