#!/usr/bin/env python3
"""xsd_generator — Infer XSD schemas from XML instance documents."""
import argparse
import json
import sys
from collections import defaultdict
from xml.etree import ElementTree as ET


def infer_element_type(text):
    if text is None or text.strip() == "":
        return "xs:string"
    stripped = text.strip()
    try:
        int(stripped)
        return "xs:integer" if "." not in stripped else None
    except ValueError:
        pass
    try:
        float(stripped)
        return "xs:decimal"
    except ValueError:
        pass
    if stripped.lower() in ("true", "false"):
        return "xs:boolean"
    import re
    if re.match(r"^\d{4}-\d{2}-\d{2}(T|\s)", stripped):
        return "xs:dateTime"
    return "xs:string"


def element_name(el):
    tag = el.tag
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def namespace_uri(el):
    tag = el.tag
    if "}" in tag:
        return tag.split("}", 1)[0].lstrip("{")
    return None


def infer_schema(sample_files, target_ns, element_form_default):
    root_elements = []
    elements = defaultdict(lambda: {"occurrences": 0, "children": defaultdict(int), "text_types": [], "attributes": {}})

    for fpath in sample_files:
        try:
            tree = ET.parse(fpath)
            root = tree.getroot()
            root_elements.append(element_name(root))
            walk(root, elements)
        except ET.ParseError as e:
            return {"error": f"Parse error in {fpath}: {e}"}

    xsd_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
    ]

    if target_ns:
        if element_form_default == "qualified":
            xsd_lines.append(
                f'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"'
                f' targetNamespace="{target_ns}"'
                f' xmlns="{target_ns}"'
                f' elementFormDefault="qualified">'
            )
        else:
            xsd_lines.append(
                f'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"'
                f' targetNamespace="{target_ns}"'
                f' xmlns:tns="{target_ns}"'
                f' elementFormDefault="unqualified">'
            )
    else:
        xsd_lines.append('<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">')

    unique_roots = list(dict.fromkeys(root_elements))
    if len(unique_roots) == 1:
        xsd_lines.append(f'  <xs:element name="{unique_roots[0]}">')
        xsd_lines.append("    <xs:complexType>")
        xsd_lines.append("      <xs:sequence>")

        el_name = unique_roots[0]
        for child_name, count in sorted(elements[el_name]["children"].items()):
            child_type = infer_element_type_for_element(child_name, elements)
            xsd_lines.append(f'        <xs:element name="{child_name}" type="{child_type}" minOccurs="0" maxOccurs="unbounded"/>')

        xsd_lines.append("      </xs:sequence>")
        xsd_lines.append("    </xs:complexType>")
        xsd_lines.append("  </xs:element>")

    for el_name, el_info in sorted(elements.items()):
        if el_name in unique_roots:
            continue
        child_types = []
        for attribute_name, attribute_values in el_info.get("attributes", {}).items():
            child_types.append(f'      <xs:attribute name="{attribute_name}" type="xs:string" use="optional"/>')
        for child_name, count in sorted(el_info["children"].items()):
            child_type = infer_element_type_for_element(child_name, elements)
            child_types.append(f'      <xs:element name="{child_name}" type="{child_type}" minOccurs="0" maxOccurs="unbounded"/>')

        has_children = bool(child_types)
        has_text = any(infer_element_type(t) != "xs:string" for t in el_info.get("text_types", []))

        if has_text and not has_children:
            inferred_type = el_info.get("text_types", ["xs:string"])[0]
            t = infer_element_type(inferred_type)
            xsd_lines.append(f'  <xs:element name="{el_name}" type="{t}"/>')
        elif has_children:
            xsd_lines.append(f'  <xs:element name="{el_name}">')
            xsd_lines.append("    <xs:complexType>")
            xsd_lines.append("      <xs:sequence>")
            xsd_lines.extend(child_types)
            xsd_lines.append("      </xs:sequence>")
            xsd_lines.append("    </xs:complexType>")
            xsd_lines.append("  </xs:element>")
        else:
            xsd_lines.append(f'  <xs:element name="{el_name}" type="xs:string"/>')

    xsd_lines.append("</xs:schema>")
    return {"xsd": "\n".join(xsd_lines), "elements_found": len(elements), "root_elements": unique_roots}


def walk(el, elements):
    name = element_name(el)
    elements[name]["occurrences"] += 1

    text = el.text.strip() if el.text else ""
    if text:
        elements[name]["text_types"].append(text)

    for attr_key, attr_val in el.attrib.items():
        if attr_key not in ("{http://www.w3.org/2001/XMLSchema-instance}schemaLocation",):
            if attr_key not in elements[name]["attributes"]:
                elements[name]["attributes"][attr_key] = []
            elements[name]["attributes"][attr_key].append(attr_val)

    for child in el:
        child_name = element_name(child)
        elements[name]["children"][child_name] += 1
        walk(child, elements)


def infer_element_type_for_element(el_name, elements):
    if el_name not in elements:
        return "xs:string"
    el_info = elements[el_name]
    text_types = el_info.get("text_types", [])
    if not text_types:
        return "xs:string"
    types = [infer_element_type(t) for t in text_types]
    unique_types = list(set(types))
    return unique_types[0] if len(unique_types) == 1 else "xs:string"


def main():
    parser = argparse.ArgumentParser(description="Generate XSD schema from XML samples")
    parser.add_argument("--samples", required=True, nargs="+", help="XML sample file paths")
    parser.add_argument("--output", required=True, help="Output .xsd file path")
    parser.add_argument("--target-namespace", default="", help="targetNamespace for generated schema")
    parser.add_argument("--element-form-default", default="qualified", choices=["qualified", "unqualified"])
    args = parser.parse_args()

    result = infer_schema(args.samples, args.target_namespace, args.element_form_default)
    print(json.dumps({k: v for k, v in result.items() if k != "xsd"}))

    if "error" in result:
        sys.exit(1)
    with open(args.output, "w") as f:
        f.write(result["xsd"])
    sys.exit(0)


if __name__ == "__main__":
    main()
