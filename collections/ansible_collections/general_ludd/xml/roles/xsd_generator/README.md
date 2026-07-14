# xsd_generator — Generate XSD schemas from XML samples

Infer XSD (XML Schema Definition) from one or more XML instance documents. Walks element trees, infers element types from text content, and generates proper schema output.

## Schema Inference Rules

- **xs:integer** — text contains only digits (no decimal point)
- **xs:decimal** — text contains digits with a decimal point
- **xs:boolean** — text is `true` or `false`
- **xs:dateTime** — text matches `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM:SS` patterns
- **xs:string** — fallback for all other text content
- Elements with no text but children → complexType with sequence
- Elements with text only → simple element with inferred type
- Elements with both text and children → complexType with mixed content

## Namespace Support

- `targetNamespace` — assigned to the generated schema and all top-level element declarations
- `elementFormDefault="qualified"` — elements must be namespace-qualified in instance documents
- `elementFormDefault="unqualified"` — only the root element needs namespace qualification
- `xmlns` declarations in sample files are detected and preserved in the schema inference

## Usage

### Basic XSD generation from a single sample

```yaml
- name: Generate XSD from an XML instance
  include_role:
    name: general_ludd.xml.xsd_generator
  vars:
    sample_files:
      - /data/order_sample.xml
    output_xsd: /schemas/order.xsd
    target_namespace: "http://example.com/orders/v1"
    element_form_default: qualified
```

### Multi-sample inference (merges element types across files)

```yaml
- name: Generate XSD from multiple samples
  include_role:
    name: general_ludd.xml.xsd_generator
  vars:
    sample_files:
      - /data/orders_001.xml
      - /data/orders_002.xml
      - /data/orders_003.xml
    output_xsd: /schemas/orders_merged.xsd
```

### No namespace, unqualified form

```yaml
- name: Generate plain XSD
  include_role:
    name: general_ludd.xml.xsd_generator
  vars:
    sample_files:
      - /data/config_sample.xml
    output_xsd: /schemas/config.xsd
    element_form_default: unqualified
```

## Output

The role writes a `xsd_generator.json` artifact to `artifact_dir` with:
- `elements_found` — count of unique elements discovered
- `root_elements` — list of root element names found across samples
- `output_xsd` — path to the generated .xsd file
