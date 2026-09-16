"""GET/HEAD: stream file bytes with Range and conditional support.

Local blobs go through werkzeug's send_file (Range/206, If-Range, 304 for
free). S3 blobs are proxied with the client's Range passed through — the
Windows mini-redirector mishandles auth on cross-host redirects, so presigned
302s are behind the drive_webdav_s3_redirect site flag for lenient-client
deployments. Collections redirect browsers into the Drive SPA.
"""

import frappe
from botocore.exceptions import ClientError
from werkzeug.utils import redirect, send_file
from werkzeug.wrappers import Response

from suite.suite_drive.utils.files import FileManager, content_disposition, storage_key, stored_on_disk
from suite.suite_drive.webdav import pathmap, perms
from suite.suite_drive.webdav.conditional import is_not_modified
from suite.suite_drive.webdav.context import DavContext
from suite.suite_drive.webdav.errors import NotFoundError
from suite.suite_drive.webdav.properties import compute_etag, modified_utc, rfc1123

S3_CHUNK = 256 * 1024


def handle(ctx: DavContext) -> Response:
    resolved = pathmap.resolve(ctx.segments, ctx.user)

    if resolved.root == "virtual" and resolved.is_mount:
        return _collection_response(ctx, "/drive")
    if not resolved.exists:
        raise NotFoundError("Resource not found.")

    row = resolved.entity
    if not perms.resolve_entity_access(row, ctx.user)["read"]:
        raise NotFoundError("Resource not found.")

    if resolved.is_collection:
        return _collection_response(ctx, f"/drive/d/{row.name}")

    manager = ctx.manager
    if manager.s3_enabled and not stored_on_disk(row.file_url):
        return _serve_s3(ctx, row, manager)
    return _serve_local(ctx, row, manager)


def _collection_response(ctx: DavContext, spa_url: str) -> Response:
    if ctx.request.method == "HEAD":
        return Response(status=200)
    # a browser landing on a collection URL gets the real UI
    return redirect(spa_url, code=302)


def _neutralize_active_content(headers, filename: str) -> None:
    """File bytes are untrusted user content served from the app origin, and
    neither frappe nor werkzeug adds these: without them an uploaded HTML/SVG
    file opened in a browser runs its scripts with the viewer's session.
    Attachment covers UAs that ignore CSP, matching the presigned-URL path;
    DAV clients name files from the URL and ignore all three headers."""
    headers["X-Content-Type-Options"] = "nosniff"
    headers["Content-Security-Policy"] = "sandbox"
    headers["Content-Disposition"] = content_disposition(filename)


def _serve_local(ctx: DavContext, row: frappe._dict, manager: FileManager) -> Response:
    try:
        response = send_file(
            manager.get_local_path(row.file_url),
            environ=ctx.request.environ,
            mimetype=row.mime_type or "application/octet-stream",
            conditional=True,
            # werkzeug re-quotes; passing the quoted form raises "invalid etag"
            etag=compute_etag(row).strip('"'),
            last_modified=modified_utc(row),
        )
    except FileNotFoundError:
        raise NotFoundError("File content is missing.") from None
    response.headers["Cache-Control"] = "private, no-cache"
    # werkzeug advertises ranges only on actual 206s
    response.headers["Accept-Ranges"] = "bytes"
    _neutralize_active_content(response.headers, row.file_name)
    return response


def _serve_s3(ctx: DavContext, row: frappe._dict, manager: FileManager) -> Response:
    key = storage_key(row.file_url)

    if frappe.conf.get("drive_webdav_s3_redirect"):
        return redirect(manager.presigned_url(key, row.file_name, row.mime_type), code=302)

    headers = {
        "ETag": compute_etag(row),
        "Last-Modified": rfc1123(row.modified),
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-cache",
    }
    _neutralize_active_content(headers, row.file_name)
    if is_not_modified(ctx.request, row):
        return Response(status=304, headers=headers)

    try:
        if ctx.request.method == "HEAD":
            meta = manager.conn.head_object(Bucket=manager.bucket, Key=key)
            headers["Content-Length"] = str(meta["ContentLength"])
            return Response(
                status=200, headers=headers, content_type=row.mime_type or "application/octet-stream"
            )

        extra = {}
        if range_header := ctx.request.headers.get("Range"):
            extra["Range"] = range_header
        obj = manager.conn.get_object(Bucket=manager.bucket, Key=key, **extra)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code == "InvalidRange":
            return Response(status=416, headers={**headers, "Content-Range": f"bytes */{row.file_size or 0}"})
        if code in ("NoSuchKey", "404"):
            raise NotFoundError("File content is missing.") from e
        raise

    body = obj["Body"]
    headers["Content-Length"] = str(obj["ContentLength"])
    if content_range := obj.get("ContentRange"):
        headers["Content-Range"] = content_range

    def stream():
        try:
            while chunk := body.read(S3_CHUNK):
                yield chunk
        finally:
            body.close()

    return Response(
        stream(),
        status=206 if "ContentRange" in obj else 200,
        headers=headers,
        content_type=row.mime_type or "application/octet-stream",
        direct_passthrough=True,
    )
