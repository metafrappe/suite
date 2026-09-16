"""MKCOL, DELETE and MOVE.

DELETE maps to Drive's trash (recoverable from the web UI) — the resource
leaves the DAV namespace either way. MOVE reuses Drive's own move/rename
controllers, pre-trashing an Overwrite:T target so their auto-rename collision
handling never fires and exact WebDAV naming survives.
"""

from pathlib import Path

import frappe
from werkzeug.wrappers import Response

from suite.suite_drive.api.files import toggle_entity_status
from suite.suite_drive.api.permissions import user_has_permission
from suite.suite_drive.utils import create_drive_file, generate_upward_path
from suite.suite_drive.utils.files import storage_key
from suite.suite_drive.webdav import locks, pathmap, perms
from suite.suite_drive.webdav.conditional import evaluate_preconditions
from suite.suite_drive.webdav.context import DavContext
from suite.suite_drive.webdav.errors import (
    Conflict,
    Forbidden,
    MethodNotAllowed,
    NotFoundError,
    PreconditionFailed,
    UnsupportedMediaType,
)


def handle_mkcol(ctx: DavContext) -> Response:
    if ctx.body.read_all(64 * 1024).strip():
        # RFC 4918 §9.3: unknown MKCOL bodies may be refused
        raise UnsupportedMediaType("MKCOL request bodies are not supported.")

    resolved = pathmap.resolve(ctx.segments, ctx.user)
    if resolved.is_mount or resolved.root == "virtual":
        raise Forbidden("Cannot create collections here.")
    if resolved.missing_intermediate or resolved.root == "unknown":
        raise Conflict("Intermediate collections do not exist.")

    parent, name = resolved.parent, ctx.segments[-1]
    # hide the whole subtree from users with no access to the parent — the
    # "already exists" (405) and permission (403) replies must not leak that a
    # sibling exists to someone who cannot even read the folder
    access = perms.resolve_entity_access(parent, ctx.user)
    if not (access["read"] or access["upload"]):
        raise NotFoundError("Resource not found.")
    if resolved.exists:
        # an existing but unreadable sibling looks absent, not "already here"
        if not perms.resolve_entity_access(resolved.entity, ctx.user)["read"]:
            raise NotFoundError("Resource not found.")
        raise MethodNotAllowed("A resource already exists at this URL.")
    if not access["upload"]:
        raise Forbidden("Ask the folder owner for upload access.")
    pathmap.validate_dav_name(name, parent)
    locks.enforce(ctx, membership_parent=parent.name)

    # create_folder minus validate_filename: exact-name collision was already
    # ruled out by resolution, and the LIKE-count heuristic both over- and
    # under-detects for DAV semantics
    path = ctx.manager.create_folder(
        frappe._dict(file_name=name, parent_path=Path(storage_key(parent.file_url or "")))
    )
    create_drive_file(name, parent.name, "Folder", path)
    return Response(status=201)


def handle_delete(ctx: DavContext) -> Response:
    resolved = pathmap.resolve(ctx.segments, ctx.user)
    if resolved.is_mount or resolved.root == "virtual":
        raise Forbidden("Cannot delete this collection.")
    if not resolved.exists:
        raise NotFoundError("Resource not found.")
    # unreadable is indistinguishable from absent (see handle_mkcol)
    if not perms.resolve_entity_access(resolved.entity, ctx.user)["read"]:
        raise NotFoundError("Resource not found.")

    evaluate_preconditions(ctx.request, resolved.entity)
    locks.enforce(
        ctx,
        entity=resolved.entity.name,
        membership_parent=resolved.entity.folder,
        check_descendants=bool(resolved.entity.is_folder),
    )

    # trash, not destruction: recoverable from the Drive web UI
    toggle_entity_status(frappe.get_doc("File", resolved.entity.name), ctx.manager, set())
    locks.drop_locks_under(resolved.entity.name)
    return Response(status=204)


def handle_move(ctx: DavContext) -> Response:
    source = pathmap.resolve(ctx.segments, ctx.user)
    if source.is_mount or source.root == "virtual":
        raise Forbidden("Cannot move this collection.")
    if not source.exists:
        raise NotFoundError("Resource not found.")
    # unreadable is indistinguishable from absent (see handle_mkcol)
    if not perms.resolve_entity_access(source.entity, ctx.user)["read"]:
        raise NotFoundError("Resource not found.")

    destination, dest_parent, dest_name = _resolve_destination(ctx, source)
    evaluate_preconditions(ctx.request, source.entity)

    if not user_has_permission(source.entity.name, "write"):
        raise Forbidden("You cannot move this file.")
    if not user_has_permission(dest_parent.name, "upload"):
        raise Forbidden("Ask the destination folder owner for upload access.")
    pathmap.validate_dav_name(dest_name, dest_parent)
    locks.enforce(
        ctx,
        entity=source.entity.name,
        membership_parent=source.entity.folder,
        check_descendants=bool(source.entity.is_folder),
    )
    locks.enforce(ctx, membership_parent=dest_parent.name)

    overwrote = False
    target = destination.entity
    if target is not None and target.name != source.entity.name:
        if not ctx.overwrite:
            raise PreconditionFailed("Destination exists and Overwrite is F.")
        locks.enforce(ctx, entity=target.name, check_descendants=bool(target.is_folder))
        # trashing frees the exact name, so Drive's controllers won't auto-rename
        toggle_entity_status(frappe.get_doc("File", target.name), ctx.manager, set())
        locks.drop_locks_under(target.name)
        overwrote = True

    doc = frappe.get_doc("File", source.entity.name)
    pathmap.reset_memo()
    if dest_parent.name != source.entity.folder:
        doc.move(dest_parent.name)
    if doc.file_name != dest_name:
        doc.rename(dest_name)

    # RFC 4918 §7.5: locks do not move with the resource
    locks.drop_locks_under(source.entity.name)
    return Response(status=204 if overwrote else 201)


def _resolve_destination(ctx: DavContext, source: pathmap.ResolvedPath):
    """Parse + resolve the Destination header; returns (resolved, parent_row, leaf_name)."""
    segments, _ = pathmap.parse_destination(ctx.request)
    destination = pathmap.resolve(segments, ctx.user)

    if destination.is_mount or destination.root == "virtual":
        raise Forbidden("Cannot write to the namespace root.")
    if destination.root == "unknown" or destination.missing_intermediate:
        raise Conflict("Destination's parent collection does not exist.")

    if destination.entity is not None and destination.entity.name == source.entity.name:
        if segments[-1] != source.entity.file_name:
            # case-only rename: the CI fallback resolved the source itself
            return destination, _parent_row(destination.entity.folder), segments[-1]
        raise Forbidden("Source and destination are the same resource.")

    parent = destination.parent if destination.parent is not None else _parent_row(destination.entity.folder)

    # a destination parent the user cannot see reads as absent, not forbidden
    dest_access = perms.resolve_entity_access(parent, ctx.user)
    if not (dest_access["read"] or dest_access["upload"]):
        raise Conflict("Destination's parent collection does not exist.")

    # an existing destination the user cannot read fails closed as PUT does
    # (404) — the Overwrite:F 412 and the overwrite path's 403 would otherwise
    # confirm a hidden name to anyone with upload access to the parent
    if (
        destination.entity is not None
        and not perms.resolve_entity_access(destination.entity, ctx.user)["read"]
    ):
        raise NotFoundError("Resource not found.")

    # a folder cannot move/copy into its own subtree
    if source.entity.is_folder:
        ancestors = {node["name"] for node in generate_upward_path(parent.name, ctx.user)}
        if source.entity.name in ancestors:
            raise Conflict("Destination is inside the source collection.")

    return destination, parent, segments[-1]


def _parent_row(name: str) -> frappe._dict:
    row = pathmap.fetch(name)
    if row is None:
        raise Conflict("Destination's parent collection does not exist.")
    return row
