"""
xml_core -- XML parsing, XPath querying, and namespace handling.

A stdlib-only (xml.etree.ElementTree) utility module shared by the
general_ludd.xml collection's roles. Provides four primitives:

    parse_xml(text)            -> ElementTree
    xpath_eval(tree, expr, ..) -> list[Element]
    extract_namespaces(root)   -> dict[str, str]   (prefix -> uri)
    normalize_namespace(tag)   -> tuple[str|None, str]

The namespace map returned by ``extract_namespaces`` is directly usable
as the ``namespaces`` argument to ``xpath_eval``.
"""

from __future__ import annotations

from xml.etree import ElementTree as ET


def parse_xml(text: str) -> ET.ElementTree:
    """Parse an XML string into an :class:`xml.etree.ElementTree.ElementTree`.

    Raises:
        xml.etree.ElementTree.ParseError: if ``text`` is not well-formed XML
            (including the empty string).
    """
    root = ET.fromstring(text)
    return ET.ElementTree(root)


def xpath_eval(
    tree: ET.ElementTree,
    expr: str,
    namespaces: dict[str, str] | None = None,
) -> list[ET.Element]:
    """Evaluate an XPath expression against ``tree``'s root.

    Args:
        tree: an :class:`ElementTree` returned by :func:`parse_xml`.
        expr: an XPath 1.0-subset expression (ElementTree-supported subset).
        namespaces: optional ``{prefix: uri}`` mapping, enabling prefixed
            queries like ``.//a:child``. Defaults to ``{}``.

    Returns:
        A list of matching :class:`Element` objects (empty on no match).
    """
    return tree.findall(expr, namespaces or {})


def extract_namespaces(root: ET.Element) -> dict[str, str]:
    """Collect every distinct namespace URI embedded in ``root``'s subtree.

    ElementTree strips ``xmlns`` declarations at parse time and stores
    element/attribute tags in Clark notation (``{uri}local``). This walker
    reverses that, returning a ``{generated_prefix: uri}`` map that can be
    passed straight to :func:`xpath_eval`.

    The default namespace (if any) gets the synthetic prefix ``ns0``;
    subsequent URIs are numbered ``ns1``, ``ns2`` in document order.
    Returns ``{}`` for plain XML with no namespaces.
    """
    seen: set[str] = set()
    ordered_uris: list[str] = []

    def _consider_tag(tag: object) -> None:
        if not isinstance(tag, str):
            return
        if not tag.startswith("{"):
            return
        uri = tag[1:].partition("}")[0]
        if uri and uri not in seen:
            seen.add(uri)
            ordered_uris.append(uri)

    for el in root.iter():
        _consider_tag(el.tag)
        for attr_name in el.attrib:
            _consider_tag(attr_name)

    return {f"ns{i}": uri for i, uri in enumerate(ordered_uris)}


def normalize_namespace(tag: str) -> tuple[str | None, str]:
    """Split a Clark-notation tag into ``(namespace_uri, local_name)``.

    ``"{http://x.example}foo"`` -> ``("http://x.example", "foo")``
    ``"bar"``                   -> ``(None, "bar")``
    ``"{}weird"``               -> ``(None, "weird")`` (graceful degradation)
    """
    if not tag.startswith("{"):
        return (None, tag)
    uri, _, local = tag[1:].partition("}")
    if not uri:
        return (None, local if local else tag)
    if not local:
        return (uri, "")
    return (uri, local)
