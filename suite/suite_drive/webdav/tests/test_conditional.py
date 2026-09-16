from datetime import datetime

import frappe
from frappe.tests import IntegrationTestCase
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from suite.suite_drive.webdav.conditional import evaluate_preconditions
from suite.suite_drive.webdav.errors import PreconditionFailed
from suite.suite_drive.webdav.properties import compute_etag, rfc1123


def request_with(**headers) -> Request:
    return Request(EnvironBuilder(method="PUT", path="/dav/x", headers=headers).get_environ())


def row() -> frappe._dict:
    return frappe._dict(
        name="cond123",
        file_size=10,
        content_hash="beef",
        modified=datetime(2026, 8, 20, 12, 0, 0),
    )


class TestPreconditions(IntegrationTestCase):
    def test_no_headers_passes(self):
        evaluate_preconditions(request_with(), row())
        evaluate_preconditions(request_with(), None)

    def test_if_match(self):
        etag = compute_etag(row())
        evaluate_preconditions(request_with(**{"If-Match": etag}), row())
        evaluate_preconditions(request_with(**{"If-Match": f'"other", {etag}'}), row())
        evaluate_preconditions(request_with(**{"If-Match": "*"}), row())

        with self.assertRaises(PreconditionFailed):
            evaluate_preconditions(request_with(**{"If-Match": '"nope"'}), row())
        with self.assertRaises(PreconditionFailed):
            evaluate_preconditions(request_with(**{"If-Match": "*"}), None)
        # weak validators never satisfy If-Match (RFC 7232 §2.3.2)
        with self.assertRaises(PreconditionFailed):
            evaluate_preconditions(request_with(**{"If-Match": f"W/{etag}"}), row())

    def test_if_none_match(self):
        etag = compute_etag(row())
        evaluate_preconditions(request_with(**{"If-None-Match": '"other"'}), row())
        evaluate_preconditions(request_with(**{"If-None-Match": "*"}), None)

        with self.assertRaises(PreconditionFailed):
            evaluate_preconditions(request_with(**{"If-None-Match": "*"}), row())
        with self.assertRaises(PreconditionFailed):
            evaluate_preconditions(request_with(**{"If-None-Match": etag}), row())
        with self.assertRaises(PreconditionFailed):
            evaluate_preconditions(request_with(**{"If-None-Match": f"W/{etag}"}), row())

    def test_if_unmodified_since(self):
        current = rfc1123(row().modified)
        evaluate_preconditions(request_with(**{"If-Unmodified-Since": current}), row())

        stale = rfc1123(datetime(2020, 1, 1))
        with self.assertRaises(PreconditionFailed):
            evaluate_preconditions(request_with(**{"If-Unmodified-Since": stale}), row())

        # garbage dates are ignored per RFC
        evaluate_preconditions(request_with(**{"If-Unmodified-Since": "not-a-date"}), row())
