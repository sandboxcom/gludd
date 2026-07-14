#!/usr/bin/env python3
"""xslt_transformer — Apply and author XSLT transformations."""
import argparse
import json
import sys
import os

try:
    from lxml import etree
    HAS_LXML = True
except ImportError:
    import xml.etree.ElementTree as ET
    HAS_LXML = False


def apply_xslt(xml_path, xslt_path, output_path, params, output_format):
    if not os.path.exists(xml_path):
        return {"success": False, "error": f"XML input not found: {xml_path}"}
    if not os.path.exists(xslt_path):
        return {"success": False, "error": f"XSLT file not found: {xslt_path}"}

    if HAS_LXML:
        return apply_xslt_lxml(xml_path, xslt_path, output_path, params, output_format)
    else:
        return {"success": False, "error": "lxml is required for XSLT transformation. Install: pip install lxml"}


def apply_xslt_lxml(xml_path, xslt_path, output_path, params, output_format):
    try:
        xml_doc = etree.parse(xml_path)
        xslt_doc = etree.parse(xslt_path)
    except etree.XMLSyntaxError as e:
        return {"success": False, "error": f"XML parse error: {e}"}

    try:
        transform = etree.XSLT(xslt_doc)
    except etree.XSLTParseError as e:
        return {"success": False, "error": f"XSLT compile error: {e}"}

    str_params = {k: etree.XSLT.strparam(v) for k, v in params.items()}

    try:
        result = transform(xml_doc, **str_params)
    except Exception as e:
        return {"success": False, "error": f"XSLT transformation error: {e}"}

    output = str(result)

    if output_format == "html":
        if not output.strip().startswith("<!DOCTYPE") and "<html" not in output.lower():
            output = "<!DOCTYPE html>\n" + output
    elif output_format == "xml":
        if not output.strip().startswith("<?xml"):
            output = '<?xml version="1.0" encoding="UTF-8"?>\n' + output

    with open(output_path, "w") as f:
        f.write(output)

    line_count = output.count("\n") + 1
    return {
        "success": True,
        "output_file": output_path,
        "output_format": output_format,
        "output_size_bytes": len(output),
        "output_lines": line_count,
        "params_used": list(params.keys()),
    }


def chain_transforms(xml_path, xslt_files, output_path, params, output_format):
    if not HAS_LXML:
        return {"success": False, "error": "lxml is required for chained XSLT transformations"}

    current = xml_path
    for i, xslt_path in enumerate(xslt_files):
        if i < len(xslt_files) - 1:
            temp_output = output_path + f".stage{i}.xml"
        else:
            temp_output = output_path
        result = apply_xslt_lxml(current, xslt_path, temp_output, params, output_format)
        if not result["success"]:
            return result
        current = temp_output

    return {
        "success": True,
        "output_file": output_path,
        "chain_length": len(xslt_files),
        "output_format": output_format,
    }


def main():
    parser = argparse.ArgumentParser(description="Apply XSLT transformations to XML")
    parser.add_argument("--xml", required=True, help="XML input file")
    parser.add_argument("--xslt", required=True, nargs="+", help="XSLT file(s); multiple files chain transformations")
    parser.add_argument("--output", required=True, help="Output file path")
    parser.add_argument("--format", default="xml", choices=["xml", "html", "text"])
    parser.add_argument("--params", default="{}", help="JSON dict of XSLT parameters")
    args = parser.parse_args()

    params = json.loads(args.params) if args.params else {}

    if len(args.xslt) == 1:
        result = apply_xslt(args.xml, args.xslt[0], args.output, params, args.format)
    else:
        result = chain_transforms(args.xml, args.xslt, args.output, params, args.format)

    print(json.dumps(result))
    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
