#!/usr/bin/env python3
"""xml_core — XML parsing, XPath querying, namespace handling."""
import argparse
import json
import sys
from xml.etree import ElementTree as ET


def register_namespaces(root, ns_map, tree=None):
    for prefix, uri in ns_map.items():
        ET.register_namespace(prefix, uri)


def extract(root, xpath_expr, ns_map):
    try:
        elements = root.findall(xpath_expr, ns_map)
    except SyntaxError as e:
        return {"found": False, "matches": [], "error": str(e)}
    results = []
    for el in elements:
        if el.text:
            results.append(el.text.strip())
        elif len(el):
            results.append(ET.tostring(el, encoding="unicode"))
        else:
            results.append(ET.tostring(el, encoding="unicode"))
    return {"found": len(results) > 0, "matches": results, "error": None}


def modify(root, xpath_expr, ns_map):
    try:
        elements = root.findall(xpath_expr, ns_map)
    except SyntaxError as e:
        return {"modified": False, "count": 0, "error": str(e)}
    for el in elements:
        el.text = "MODIFIED"
        el.set("modified", "true")
    return {"modified": len(elements) > 0, "count": len(elements), "error": None}


def validate_xml(filepath):
    try:
        tree = ET.parse(filepath)
        return {"parsed": True, "error": None, "root_tag": ET.tostring(tree.getroot(), encoding="unicode").split(">")[0].lstrip("<").split()[0] if tree.getroot() is not None else ""}
    except ET.ParseError as e:
        return {"parsed": False, "error": str(e), "root_tag": None}


def main():
    parser = argparse.ArgumentParser(description="xml_core XML operations")
    parser.add_argument("--input", required=True)
    parser.add_argument("--operation", required=True, choices=["extract", "modify", "validate"])
    parser.add_argument("--output", default="/tmp/gludd-xml-core/output.xml")
    parser.add_argument("--xpath", default="")
    parser.add_argument("--namespaces", default="{}")
    args = parser.parse_args()

    ns_map = json.loads(args.namespaces) if args.namespaces else {}

    if args.operation == "validate":
        result = validate_xml(args.input)
        print(json.dumps(result))
        sys.exit(0 if result["parsed"] else 1)

    tree = ET.parse(args.input)
    root = tree.getroot()

    register_namespaces(root, ns_map)

    if args.operation == "extract":
        result = extract(root, args.xpath, ns_map)
        print(json.dumps(result))
        if result["found"]:
            with open(args.output, "w") as f:
                for match in result["matches"]:
                    f.write(match + "\n")
        sys.exit(0 if not result.get("error") else 1)

    elif args.operation == "modify":
        result = modify(root, args.xpath, ns_map)
        print(json.dumps(result))
        if result["modified"]:
            tree.write(args.output, encoding="unicode", xml_declaration=True)
        sys.exit(0 if not result.get("error") else 1)


if __name__ == "__main__":
    main()
