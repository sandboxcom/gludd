# `general_ludd.xml.plist_parser` — Apple Property Lists

Read, write, and convert Apple plist files (XML and binary) using Python's plistlib with dot-path key navigation.

## Quick start

```yaml
- name: Read a plist value
  hosts: localhost
  vars:
    plist_parser_file: "/Library/Preferences/com.apple.example.plist"
    plist_parser_operation: "read"
    plist_parser_key_path: "Root.Preferences.DisplayName"
  roles:
    - general_ludd.xml.plist_parser
```

## Operations

| Operation | Description |
|---|---|
| `read` | Extract a value by dot-path, or dump entire plist |
| `write` | Set a value by dot-path, write updated file |
| `convert` | Convert between XML and binary plist format |

## Parameters

| Variable | Default | Description |
|---|---|---|
| `plist_parser_file` | `""` | Path to plist file |
| `plist_parser_operation` | `"read"` | `read`, `write`, or `convert` |
| `plist_parser_key_path` | `""` | Dot-path to key (e.g. `dict.nested.0.index`) |
| `plist_parser_value` | `""` | New value to write |
| `plist_parser_output_format` | `"xml"` | Target format: `"xml"` or `"binary"` |

## Key paths

Navigate nested structures with dot notation:

```yaml
# Top-level key
plist_parser_key_path: "CFBundleName"

# Nested dict
plist_parser_key_path: "Root.Preferences.Theme"

# List index
plist_parser_key_path: "items.2.name"
```

## Results

```python
# read
{"format": "xml", "operation": "read", "key_path": "CFBundleName", "value": "MyApp"}

# write / convert
{"format": "binary", "operation": "convert", "output_path": "/tmp/.../com.apple.example.plist", "converted_to": "xml"}
```

## Type preservation

The role preserves native plist types: `date`, `data` (bytes), `bool`, `integer`, `real`, `array`, `dict`, and `UID` (binary plist).
