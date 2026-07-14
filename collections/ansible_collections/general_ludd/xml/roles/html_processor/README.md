# `general_ludd.xml.html_processor` — HTML Parsing & Manipulation

Parse and manipulate HTML documents using CSS selectors, XPath, and tolerant parsers (BeautifulSoup or lxml.html).

## Quick start

```yaml
- name: Extract data from HTML
  hosts: localhost
  vars:
    html_processor_url: "https://example.com"
    html_processor_css_selector: "div.article h2"
    html_processor_operation: "extract"
  roles:
    - general_ludd.xml.html_processor
```

## Operations

| Operation | Description |
|---|---|
| `extract` | Extract elements matching CSS selector or XPath |
| `modify` | Change element attributes or text content |
| `clean` | Strip scripts, styles, comments; extract text |

## Parameters

| Variable | Default | Description |
|---|---|---|
| `html_processor_url` | `""` | URL to fetch HTML from |
| `html_processor_file` | `""` | Local HTML file path |
| `html_processor_css_selector` | `""` | CSS selector for target elements |
| `html_processor_xpath_query` | `""` | XPath 1.0 query (lxml only) |
| `html_processor_operation` | `"extract"` | `extract`, `modify`, or `clean` |
| `html_processor_use_lxml` | `false` | Use lxml.html instead of BeautifulSoup |
| `html_processor_namespaces` | `{}` | XML namespaces for XPath (lxml only) |
| `html_processor_attribute` | `""` | Attribute name to modify |
| `html_processor_new_value` | `""` | New value for attribute/text |
| `html_processor_cleaner_opts` | see defaults | lxml Cleaner options (when use_lxml=true) |
| `html_processor_output_file` | `"/tmp/..."` | JSON output path |

## Results

The `html_processor_result` fact contains:
```python
{
    "operation": "extract",
    "match_count": 3,
    "output": ["<h2>...</h2>", "<h2>...</h2>", "<h2>...</h2>"]
}
```
