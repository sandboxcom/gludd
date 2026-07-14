# general_ludd.xml

Ansible collection for core XML processing — parsing, XPath querying, namespace handling, XSD schema generation, and XSLT transformation.

## Philosophy

XML is still the backbone of enterprise data interchange. This collection treats XML as structured data, not as a black box. Every operation is observable: inputs are validated, outputs are written as artifacts, and errors are surfaced with context.

## Roles

| Role | Purpose |
|------|---------|
| `xml_core` | Parse XML files, execute XPath queries, manage namespaces, extract/modify elements, validate well-formedness |
| `xsd_generator` | Infer XSD schemas from XML instance documents; generate .xsd output with namespace support |
| `xslt_transformer` | Apply XSLT to XML, chain transformations, generate output formats (HTML/XML/text) |

## Dependencies

- Python `xml.etree.ElementTree` (stdlib) — used for basic parsing and XPath in xml_core
- Python `lxml` (optional) — used for XSD generation and XSLT transformation (full XPath 1.0, XSLT 1.0 support)
- `general_ludd.agent` >= 0.1.0 (provides `gludd_model_call`, `gludd_message`, `gludd_facts`)

## Namespace Handling

All roles support XML namespaces:
- `xmlns` declarations in source documents
- `xsi:schemaLocation` for schema hints in XSD generation
- `targetNamespace` for generated schemas
- `elementFormDefault` (qualified vs unqualified) for schema output
- Namespace prefix-to-URI mappings in XPath queries via `namespaces` dict
