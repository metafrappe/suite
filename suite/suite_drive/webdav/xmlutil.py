"""Hardened XML parsing and multistatus building on lxml.

Namespace URIs are contractual, prefixes are not: we always emit DAV: as `D`
but accept any prefix from clients.
"""

from http.client import responses as HTTP_REASONS

from lxml import etree
from werkzeug.wrappers import Response

from suite.suite_drive.webdav.errors import BadRequest, UnsupportedMediaType

DAV_NS = "DAV:"
XML_BODY_CAP = 1024 * 1024

_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    huge_tree=False,
    recover=False,
)


def dav(name: str) -> str:
    """Clark notation for a DAV: name."""
    return f"{{{DAV_NS}}}{name}"


def dav_element(name: str, *children: etree._Element, text: str | None = None) -> etree._Element:
    element = etree.Element(dav(name), nsmap={"D": DAV_NS})
    if text is not None:
        element.text = text
    for child in children:
        element.append(child)
    return element


def parse_xml(body: bytes) -> etree._Element | None:
    """Parse a request body; None when empty. DOCTYPE/entities are rejected."""
    if not body or not body.strip():
        return None
    if len(body) > XML_BODY_CAP:
        raise UnsupportedMediaType("XML request body too large.")
    if b"<!DOCTYPE" in body or b"<!ENTITY" in body:
        raise BadRequest("DOCTYPE declarations are not allowed.")
    try:
        return etree.fromstring(body, parser=_PARSER)
    except etree.XMLSyntaxError as e:
        raise BadRequest(f"Malformed XML request body: {e}") from e


def parse_fragment(xml_text: str) -> etree._Element | None:
    """Re-parse a stored, previously validated fragment; None when it no
    longer parses (a row predating a policy change, or hand-edited)."""
    try:
        return etree.fromstring(xml_text.encode("utf-8"), parser=_PARSER)
    except etree.XMLSyntaxError:
        return None


def serialize(element: etree._Element) -> bytes:
    return etree.tostring(element, xml_declaration=True, encoding="utf-8")


def xml_response(element: etree._Element, status: int = 207) -> Response:
    return Response(
        serialize(element),
        status=status,
        content_type='application/xml; charset="utf-8"',
    )


def status_line(code: int) -> str:
    return f"HTTP/1.1 {code} {HTTP_REASONS.get(code, 'Unknown')}"


class MultistatusBuilder:
    def __init__(self):
        self.root = etree.Element(dav("multistatus"), nsmap={"D": DAV_NS})

    def add_response(self, href: str) -> ResponseBuilder:
        response = etree.SubElement(self.root, dav("response"))
        etree.SubElement(response, dav("href")).text = href
        return ResponseBuilder(response)

    def build(self) -> Response:
        return xml_response(self.root)


class ResponseBuilder:
    def __init__(self, element: etree._Element):
        self.element = element

    def propstat(self, status: int, props: list[etree._Element]) -> None:
        if not props:
            return
        propstat = etree.SubElement(self.element, dav("propstat"))
        prop = etree.SubElement(propstat, dav("prop"))
        for element in props:
            prop.append(element)
        etree.SubElement(propstat, dav("status")).text = status_line(status)

    def status(self, code: int) -> None:
        etree.SubElement(self.element, dav("status")).text = status_line(code)

    def error(self, condition: str) -> None:
        error = etree.SubElement(self.element, dav("error"))
        etree.SubElement(error, dav(condition))
