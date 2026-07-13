# `general_ludd.xml.docbook_converter` — DocBook & DITA

Transform DocBook XML and DITA documents to HTML, PDF, or ePub using available system tools.

## Quick start

```yaml
- name: Convert DocBook to HTML
  hosts: localhost
  vars:
    docbook_converter_input_file: "/tmp/manual.xml"
    docbook_converter_output_format: "html"
  roles:
    - general_ludd.xml.docbook_converter
```

## Output formats

| Format | Requires |
|---|---|
| `html` | `pandoc`, `xmlto`, or `xsltproc` |
| `pdf` | `pandoc` with `xelatex` |
| `epub` | `pandoc` |

## Parameters

| Variable | Default | Description |
|---|---|---|
| `docbook_converter_input_file` | `""` | DocBook XML or DITA file path |
| `docbook_converter_output_format` | `"html"` | `html`, `pdf`, or `epub` |
| `docbook_converter_profile_attrs` | `{}` | Profiling filters (e.g. `{"os": "linux"}`) |
| `docbook_converter_stylesheet` | `""` | Custom XSLT stylesheet path |
| `docbook_converter_artifact_dir` | `"/tmp/..."` | Output directory |

## Profiling

Conditional content filtering via profiling attributes:

```yaml
docbook_converter_profile_attrs:
  os: "linux"
  arch: "x86_64"
  userlevel: "admin"
```

Elements with mismatched profiling attributes are removed before conversion.

## Results

```python
{
    "format": "docbook",
    "output_format": "html",
    "method": "pandoc",
    "output_path": "/tmp/gludd-xml/docbook_converter/manual.html",
    "success": true
}
```
