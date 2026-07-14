# `general_ludd.xml.gradle_parser` — Gradle Build Files

Parse and modify Gradle build files (build.gradle, settings.gradle) using regex-based DSL extraction. Handles both Groovy and Kotlin DSL syntax.

## Quick start

```yaml
- name: List all dependencies
  hosts: localhost
  vars:
    gradle_parser_file: "/path/to/build.gradle"
    gradle_parser_operation: "list-deps"
  roles:
    - general_ludd.xml.gradle_parser
```

## Operations

| Operation | Description |
|---|---|
| `list-deps` | List all dependencies, plugins, and repositories |
| `update-version` | Change version for a specific dependency |
| `add-dep` | Add a new dependency to the file |
| `report` | Generate a markdown dependency report |

## Parameters

| Variable | Default | Description |
|---|---|---|
| `gradle_parser_file` | `""` | Path to build.gradle or settings.gradle |
| `gradle_parser_operation` | `"list-deps"` | Operation to perform |
| `gradle_parser_dep_group` | `""` | Dependency group (for update/add) |
| `gradle_parser_dep_name` | `""` | Dependency artifact name |
| `gradle_parser_new_version` | `""` | New version string |
| `gradle_parser_dep_configuration` | `"implementation"` | Configuration for new dependency |

## Examples

```yaml
# Update a dependency version
gradle_parser_operation: "update-version"
gradle_parser_dep_group: "org.springframework.boot"
gradle_parser_dep_name: "spring-boot-starter-web"
gradle_parser_new_version: "3.2.0"

# Add a new dependency
gradle_parser_operation: "add-dep"
gradle_parser_dep_group: "com.google.guava"
gradle_parser_dep_name: "guava"
gradle_parser_new_version: "33.0.0-jre"
```

## Results

```python
# list-deps
{"dependencies": [{"group": "...", "name": "...", "version": "..."}], "count": 42, "plugins": [...], "repositories": [...]}

# update-version
{"version_updated": 1, "old_version": "3.1.5", "new_version": "3.2.0", "updated_file": "/tmp/..."}

# report
{"report_path": "/tmp/.../dependency_report.md", "dependency_count": 42}
```
