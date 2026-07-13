# xml_core — XML parsing, XPath querying, namespace handling

Parse XML files, execute XPath queries, manage namespace maps, and extract or modify elements.

## Operations

| Operation | Description |
|-----------|-------------|
| `extract` | Run XPath query against XML and write matching elements/text to output |
| `modify` | Run XPath query, modify matching elements (set text, set attribute, remove), write back |
| `validate` | Check well-formedness and optionally validate against an XSD schema |

## Usage

### Extract elements via XPath

```yaml
- name: Extract title elements from an OAI-PMH response
  include_role:
    name: general_ludd.xml.xml_core
  vars:
    input_file: /data/oai_pmh_response.xml
    xpath_query: "//oai:record/oai:metadata/dc:title/text()"
    namespaces:
      oai: "http://www.openarchives.org/OAI/2.0/"
      dc: "http://purl.org/dc/elements/1.1/"
    operation: extract
    output_file: /tmp/titles.txt
```

### Extract with namespace-qualified elements

```yaml
- name: Query MODS metadata
  include_role:
    name: general_ludd.xml.xml_core
  vars:
    input_file: /data/mods_record.xml
    xpath_query: "//mods:titleInfo/mods:title/text()"
    namespaces:
      mods: "http://www.loc.gov/mods/v3"
      xsi: "http://www.w3.org/2001/XMLSchema-instance"
    operation: extract
```

### Modify an element

```yaml
- name: Update a configuration value
  include_role:
    name: general_ludd.xml.xml_core
  vars:
    input_file: /etc/app/config.xml
    xpath_query: "//app:connection/app:timeout"
    namespaces:
      app: "urn:example:app:v1"
    operation: modify
    output_file: /tmp/config-updated.xml
```

### Validate well-formedness

```yaml
- name: Check XML well-formedness
  include_role:
    name: general_ludd.xml.xml_core
  vars:
    input_file: /data/mystery_file.xml
    operation: validate
```

## Namespace Handling

- Use the `namespaces` dict to map prefixes to URIs when querying XML with namespaces
- The role passes namespaces to both `xml.etree.ElementTree` and `lxml` XPath evaluators
- `xsi:schemaLocation` attributes are preserved during modify operations
- If no namespaces are provided, the role attempts to extract namespace declarations from the root element
