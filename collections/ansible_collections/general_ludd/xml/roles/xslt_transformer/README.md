# xslt_transformer — Apply and author XSLT transformations

Apply XSL transformations to XML documents. Supports single transforms, chained pipeline transforms, and multiple output formats (HTML, XML, text). Parameters can be passed into the XSLT stylesheet at runtime.

## Requirements

Requires `lxml` (Python package). Install: `pip install lxml`

lxml provides full XSLT 1.0 support and the EXSLT extension libraries (date, math, regex, set, string).

## Output Formats

| Format | Description |
|--------|-------------|
| `xml` | Default. Transformed XML output with `<?xml?>` declaration |
| `html` | HTML output with `<!DOCTYPE html>` prepended if not present |
| `text` | Plain text output from the XSLT result |

## Usage

### XML → HTML (e.g., documentation generation)

```yaml
- name: Transform API spec XML to HTML documentation
  include_role:
    name: general_ludd.xml.xslt_transformer
  vars:
    xslt_file: /templates/api-to-html.xsl
    xml_input: /spec/api-spec.xml
    output_file: /docs/api-reference.html
    output_format: html
    params:
      page_title: "API Reference v2.1"
      css_path: "/assets/docs.css"
```

### XML → CSV (e.g., data extraction)

```yaml
- name: Extract records from XML to CSV
  include_role:
    name: general_ludd.xml.xslt_transformer
  vars:
    xslt_file: /templates/records-to-csv.xsl
    xml_input: /data/inventory.xml
    output_file: /exports/inventory.csv
    output_format: text
    params:
      delimiter: ","
      include_header: "true"
```

### XML → XML restructuring (e.g., format conversion)

```yaml
- name: Convert MODS to Dublin Core
  include_role:
    name: general_ludd.xml.xslt_transformer
  vars:
    xslt_file: /templates/mods-to-dc.xsl
    xml_input: /data/mods_record.xml
    output_file: /tmp/dc_output.xml
    output_format: xml
    params:
      dc_namespace: "http://purl.org/dc/elements/1.1/"
```

### Chained transformations (pipeline)

```yaml
- name: Chain multiple XSLTs (clean → enrich → format)
  include_role:
    name: general_ludd.xml.xslt_transformer
  vars:
    xslt_file:
      - /templates/strip-namespaces.xsl
      - /templates/add-metadata.xsl
      - /templates/format-for-display.xsl
    xml_input: /data/raw_feed.xml
    output_file: /exports/cleaned_feed.html
    output_format: html
```

## Parameters

Pass runtime parameters to the XSLT stylesheet as key-value pairs:

```yaml
params:
  sort_key: "date"
  max_items: "50"
  locale: "en_US"
```

In the XSLT, access them as:

```xml
<xsl:param name="sort_key" select="'title'"/>
<xsl:param name="max_items" select="'100'"/>
```

## Output

The role writes an `xslt_transformer.json` artifact to `artifact_dir` with:
- `output_file` — path to the generated output
- `output_format` — format used (xml, html, text)
- `output_size_bytes` — size of the output file
- `output_lines` — line count in the output
- `chain_length` — number of XSLT files in the chain
- `params_used` — list of parameter keys passed to the transform
