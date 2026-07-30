"""general_ludd.xml collection module_utils — XML parsing/XPath/namespace utilities."""

from .xml_core import (
    extract_namespaces,
    normalize_namespace,
    parse_xml,
    xpath_eval,
)

__all__ = [
    "extract_namespaces",
    "normalize_namespace",
    "parse_xml",
    "xpath_eval",
]
