# XML Collection — `general_ludd.xml`

Comprehensive Ansible collection for XML processing — parsing, XPath querying,
namespace handling, XSD schema generation, XSLT transformation, and
format-specific document manipulation. Covers the full XML document lifecycle
from well-formedness validation to structured data extraction to transformation.

## Architecture Overview

```text
┌──────────────────────────────────────────────────────────────────┐
│                      Agent / Playbook                            │
│        (FQCN: general_ludd.xml.xml_core)                         │
│        (FQCN: general_ludd.xml.xsd_generator)                    │
│        (FQCN: general_ludd.xml.xslt_transformer)                 │
└───────────────┬──────────────────┬──────────────────┬────────────┘
                │                  │                  │
    ┌───────────▼───────────┐ ┌───▼───────────┐ ┌───▼───────────────┐
    │   xml_core role       │ │ xsd_generator │ │ xslt_transformer  │
    │  (extract, modify,    │ │ (infer XSD    │ │ (apply XSLT,      │
    │   validate)           │ │  from XML)    │ │  chain transforms) │
    └───────────┬───────────┘ └───┬───────────┘ └───┬───────────────┘
                │                 │                  │
    ┌───────────▼─────────────────▼──────────────────▼──────────────┐
    │                     Python Libraries                          │
    │  xml.etree.ElementTree (stdlib) — basic parse/XPath in xml_core│
    │  lxml — XSD generation, XSLT 1.0, full XPath 1.0 support      │
    │  plistlib (stdlib) — Apple Property List read/write           │
    └───────────────────────────────────────────────────────────────┘
```

### Data flow

1. **Agent** invokes a role via FQCN with format-appropriate variables
2. **Role** reads the input file, delegates heavy computation to
   `gludd_make` or inline Python execution
3. **Python libraries** (xml.etree, lxml, plistlib) parse, query, transform,
   or validate the document
4. **Artifacts** are written to `artifact_dir` (`/tmp/gludd-xml-core/` by
   default) — output files, extracted values, generated schemas

## Section 1: Collection Overview

XML remains the backbone of enterprise data interchange, system configuration,
and document authoring. This collection provides a unified Ansible interface
to XML operations that would otherwise require scattered Python scripts or
specialized command-line tools.

### Why XML matters

| Domain | XML Role | Examples |
|--------|----------|----------|
| **Configuration files** | Structured key-value with hierarchy | pom.xml, web.config, server.xml, AndroidManifest.xml |
| **Data interchange** | Self-describing, schema-validated messages | SOAP envelopes, OAI-PMH, RSS/Atom, SAML assertions |
| **Web services** | Contract-first API design | WSDL descriptions, SOAP request/response |
| **Document formats** | Semantic markup with metadata | DocBook, DITA, XHTML, ODF, Office Open XML |
| **Build systems** | Declarative dependency and build specs | Gradle build files, Ant scripts, Maven POMs |
| **Platform preferences** | Key-value with typed values | macOS/iOS plist files |
| **Identity federation** | Signed security assertions | SAML metadata, IdP/SP configuration |

### Collection scope

The `general_ludd.xml` collection provides three roles:

| Role | Purpose | Python Backend |
|------|---------|----------------|
| `xml_core` | Parse, XPath query, namespace handling, extract/modify/validate | `xml.etree.ElementTree` (stdlib), `lxml` (optional) |
| `xsd_generator` | Infer XSD schemas from XML instance documents | `lxml` |
| `xslt_transformer` | Apply XSLT transformations, chain pipelines | `lxml` |

System-level macOS plist read/write uses `plistlib` (stdlib) and is
documented here as an XML-adjacent workflow — it does not have its own
collection role.

## Section 2: Schema / Format Reference Table

The collection recognizes these XML-based formats. Each format has a defined
schema, a usage domain, and a recommended role or approach for handling it.

### 2.1 XML Core — W3C XML 1.0 / 1.1

| Property | Value |
|----------|-------|
| **Schema** | W3C XML 1.0 (Fifth Edition) / XML 1.1 (Second Edition) |
| **Namespace** | `http://www.w3.org/XML/1998/namespace` (`xml:` prefix) |
| **Primary use** | Configuration files, data exchange, document markup |
| **Role** | `general_ludd.xml.xml_core` (extract, modify, validate) |
| **Backend** | `xml.etree.ElementTree` (stdlib), `lxml` (rich XPath) |

XML Core is the universal format — the lowest common denominator for
structured text with tags. The `xml_core` role supports:

- Well-formedness checking (parse without error)
- XPath 1.0 queries via `ElementTree` (limited predicate support)
- Full XPath 1.0 queries via `lxml` (all axes, functions, predicates)
- Element text extraction, attribute reading, element removal
- Namespace prefix-to-URI mapping for qualified element names

**Example**: Extract database connection strings from a Java web.xml:

```yaml
- name: Extract JDBC URLs from web.xml
  include_role:
    name: general_ludd.xml.xml_core
  vars:
    input_file: /opt/app/WEB-INF/web.xml
    xpath_query: "//web:resource-ref/web:res-ref-name[contains(text(), 'jdbc')]/../web:jndi-name/text()"
    namespaces:
      web: "http://java.sun.com/xml/ns/javaee"
    operation: extract
    output_file: /tmp/jdbc_connections.txt
```

### 2.2 XSD — W3C XML Schema

| Property | Value |
|----------|-------|
| **Schema** | W3C XML Schema 1.0 / 1.1 |
| **Namespace** | `http://www.w3.org/2001/XMLSchema` (`xs:` prefix) |
| **Primary use** | Schema validation, code generation, contract definition |
| **Role** | `general_ludd.xml.xsd_generator` (infer from instances) |
| **Backend** | `lxml` |

XSD defines the structure, types, and constraints of an XML document class.
The `xsd_generator` role infers an XSD from one or more instance documents:

- Infers element names, cardinality (minOccurs/maxOccurs)
- Detects simple vs complex types (text-only vs child-element content)
- Preserves namespace declarations and `targetNamespace`
- Outputs `elementFormDefault` based on instance qualification
- Handles `xsi:schemaLocation` hints in source documents

**Example**: Generate a schema from a set of SOAP response samples:

```yaml
- name: Infer XSD from SOAP response corpus
  include_role:
    name: general_ludd.xml.xsd_generator
  vars:
    input_files:
      - /data/soap_responses/GetCustomer_001.xml
      - /data/soap_responses/GetCustomer_002.xml
      - /data/soap_responses/GetCustomer_003.xml
    target_namespace: "http://example.com/customer/v1"
    output_file: /tmp/customer_response.xsd
```

### 2.3 XSLT — W3C XSLT 1.0 / 2.0 / 3.0

| Property | Value |
|----------|-------|
| **Schema** | W3C XSLT 1.0 / 2.0 / 3.0 |
| **Namespace** | `http://www.w3.org/1999/XSL/Transform` (`xsl:` prefix) |
| **Primary use** | Document transformation, report generation, format conversion |
| **Role** | `general_ludd.xml.xslt_transformer` (apply, chain) |
| **Backend** | `lxml` (XSLT 1.0); Saxon (XSLT 2.0/3.0, external) |

XSLT transforms XML into XML, HTML, or plain text via template rules. The
`xslt_transformer` role applies a stylesheet to an input document:

- XSLT 1.0 via `lxml` (bundled, no external dependency)
- XSLT 2.0/3.0 via Saxon (external, opt-in)
- Chained transformations (output of step 1 is input to step 2)
- Parameter passing (`xsl:param`) for runtime values
- Output serialization (XML, HTML, text)

**Example**: Convert internal XML report data to HTML:

```yaml
- name: Generate HTML report from XML data
  include_role:
    name: general_ludd.xml.xslt_transformer
  vars:
    input_file: /data/monthly_report.xml
    stylesheet: /etc/xslt/report_to_html.xsl
    output_format: html
    params:
      report_title: "Q3 2026 Summary"
      author_name: "Automation Pipeline"
    output_file: /tmp/report.html
```

### 2.4 HTML — WHATWG HTML5 / W3C HTML4

| Property | Value |
|----------|-------|
| **Schema** | WHATWG HTML Living Standard / W3C HTML 4.01 |
| **Namespace** | `http://www.w3.org/1999/xhtml` (XHTML only) |
| **Primary use** | Web content, email, documentation output |
| **Role** | `general_ludd.xml.xml_core` (extract, when well-formed XHTML) |
| **Backend** | `lxml.html` (tag-soup tolerant parser) |

HTML is the universal presentation format. The collection handles HTML as an
XML-adjacent format — XHTML (the XML serialization of HTML) is directly
parseable by XML tools; tag-soup HTML needs `lxml.html` for parsing.

**Workflow**: Extract all links from an HTML page:

```yaml
- name: Extract href attributes from HTML
  include_role:
    name: general_ludd.xml.xml_core
  vars:
    input_file: /tmp/page.html
    xpath_query: "//a/@href"
    operation: extract
    output_file: /tmp/links.txt
```

**Common use**: XSLT-generated HTML output (DocBook → XSLT → HTML
documentation pipeline), web scraping of well-formed pages, email template
assembly from XML data.

### 2.5 SOAP — W3C SOAP 1.1 / 1.2

| Property | Value |
|----------|-------|
| **Schema** | W3C SOAP 1.1 / SOAP 1.2 |
| **Namespace** | SOAP 1.1: `http://schemas.xmlsoap.org/soap/envelope/`; SOAP 1.2: `http://www.w3.org/2003/05/soap-envelope` |
| **Primary use** | Enterprise web services, legacy API integration |
| **Role** | `general_ludd.xml.xml_core` (extract body, check faults) |
| **Backend** | `xml.etree.ElementTree` (stdlib), `lxml` |

SOAP envelopes carry XML payloads (the "body") wrapped in a standard
container with optional headers. The collection handles:

- Envelope unwrapping: extract the `soap:Body` children
- Fault detection: check for `soap:Fault` elements
- Header extraction: read WS-Security, WS-Addressing headers
- Body validation: XPath queries against the unwrapped payload

**Example**: Extract a SOAP response body and convert to JSON:

```yaml
- name: Extract SOAP body
  include_role:
    name: general_ludd.xml.xml_core
  vars:
    input_file: /data/soap_response.xml
    xpath_query: "//soap:Body/*"
    namespaces:
      soap: "http://schemas.xmlsoap.org/soap/envelope/"
    operation: extract
    output_file: /tmp/soap_body.xml

- name: Transform SOAP body to JSON via XSLT
  include_role:
    name: general_ludd.xml.xslt_transformer
  vars:
    input_file: /tmp/soap_body.xml
    stylesheet: /etc/xslt/xml_to_json.xsl
    output_format: text
    output_file: /tmp/soap_as_json.json
```

### 2.6 SAML — OASIS SAML 2.0

| Property | Value |
|----------|-------|
| **Schema** | OASIS Security Assertion Markup Language 2.0 |
| **Namespace** | `urn:oasis:names:tc:SAML:2.0:assertion` (`saml:` or `saml2:` prefix) |
| **Primary use** | SSO authentication, identity federation, attribute exchange |
| **Role** | `general_ludd.xml.xml_core` (extract assertions, attributes) |
| **Backend** | `xml.etree.ElementTree` (stdlib), `lxml` |

SAML assertions carry signed identity claims between an Identity Provider
(IdP) and a Service Provider (SP). The collection handles:

- Assertion extraction: pull `saml:Assertion` elements from responses
- Attribute extraction: read `saml:Attribute` name/value pairs
- Subject confirmation: check `saml:Subject/saml:NameID` values
- Condition checking: verify `NotBefore` / `NotOnOrAfter` timestamps
- Metadata parsing: read IdP/SP metadata for endpoint discovery

> **Note**: Signature verification requires `xmlsec` / `signxml` (Python
> libraries for XML Digital Signature). The collection does not bundle
> cryptographic validation; it focuses on structural extraction and
> namespace-aware querying.

**Example**: Extract user attributes from a SAML assertion:

```yaml
- name: Read SAML attributes
  include_role:
    name: general_ludd.xml.xml_core
  vars:
    input_file: /data/saml_response.xml
    xpath_query: "//saml2:AttributeStatement/saml2:Attribute"
    namespaces:
      saml2: "urn:oasis:names:tc:SAML:2.0:assertion"
      saml2p: "urn:oasis:names:tc:SAML:2.0:protocol"
    operation: extract
    output_file: /tmp/saml_attributes.xml
```

### 2.7 DocBook — OASIS DocBook 5.x

| Property | Value |
|----------|-------|
| **Schema** | OASIS DocBook 5.0 / 5.1 / 5.2 |
| **Namespace** | `http://docbook.org/ns/docbook` (`db:` prefix) |
| **Primary use** | Technical documentation, books, articles, man pages |
| **Role** | `general_ludd.xml.xslt_transformer` (transform to HTML/PDF) |
| **Backend** | `lxml` (XSLT 1.0); DocBook XSL stylesheets (external) |

DocBook is the standard XML vocabulary for technical publishing. The
collection provides:

- Validate DocBook 5.x against the official RNG/XSD schema
- Apply DocBook XSL stylesheets for HTML, PDF (via FO), EPUB output
- Extract metadata: `db:title`, `db:author`, `db:pubdate`
- Cross-reference resolution: resolve `xml:id` / `linkend` references
- Chunked output: generate one HTML file per chapter/section

**Example**: Convert DocBook book to multi-page HTML:

```yaml
- name: Generate chunked HTML documentation
  include_role:
    name: general_ludd.xml.xslt_transformer
  vars:
    input_file: /docs/book.xml
    stylesheet: /usr/share/xml/docbook/xsl-stylesheets/xhtml/chunk.xsl
    params:
      base.dir: "/tmp/output/"
      use.id.as.filename: "1"
      chunk.section.depth: "3"
    output_format: html
```

### 2.8 DITA — OASIS DITA 1.3

| Property | Value |
|----------|-------|
| **Schema** | OASIS Darwin Information Typing Architecture 1.3 |
| **Namespace** | `http://docs.oasis-open.org/dita/ns/architecture/2005` (DITA 1.3) |
| **Primary use** | Modular technical content, topic-based authoring |
| **Role** | `general_ludd.xml.xml_core` (extract topics, resolve maps) |
| **Backend** | `xml.etree.ElementTree` (stdlib), `lxml` |

DITA is a topic-based documentation standard used in enterprise
documentation suites. The collection handles:

- Ditamap resolution: walk a `.ditamap` file to enumerate all topics
- Topic extraction: extract `concept`, `task`, `reference` topics
- Conref resolution: resolve `conref` attribute references across topics
- Metadata extraction: `prolog`, `metadata`, `audience` elements
- Conditional processing: `props` attribute filtering (audience, platform,
  product)

**Example**: Enumerate all topics referenced by a ditamap:

```yaml
- name: Resolve DITA map topics
  include_role:
    name: general_ludd.xml.xml_core
  vars:
    input_file: /docs/master.ditamap
    xpath_query: "//topicref/@href"
    operation: extract
    output_file: /tmp/topic_list.txt
```

### 2.9 Gradle — Gradle DSL (Groovy / Kotlin)

| Property | Value |
|----------|-------|
| **Schema** | Gradle DSL (Groovy `build.gradle`, Kotlin `build.gradle.kts`) |
| **Namespace** | `http://www.gradle.org/schemas/gradle/1.0` (partial XML export via `gradle properties`) |
| **Primary use** | Java / Kotlin / Android build configuration |
| **Role** | `general_ludd.xml.xml_core` (parse Gradle XML exports) |
| **Backend** | `xml.etree.ElementTree` (stdlib) |

Gradle build files are Groovy/Kotlin scripts, not XML — but Gradle can
export its resolved model as XML via the `gradle properties --format xml`
command. The collection handles:

- Parse `gradle dependencies --format xml` output
- Extract dependency versions (group:artifact:version coordinates)
- Audit dependency trees for known-vulnerable versions
- Compare resolved vs declared version mismatches
- Inject version properties into `gradle.properties` for global upgrades

**Example**: Extract all dependency coordinates from a Gradle XML export:

```yaml
- name: Parse Gradle dependency XML export
  include_role:
    name: general_ludd.xml.xml_core
  vars:
    input_file: /data/gradle_dependencies.xml
    xpath_query: "//dependency/@group"
    operation: extract
    output_file: /tmp/gradle_groups.txt
```

**Programmatic version bump workflow**:

```bash
# Export dependencies as XML
gradle dependencies --configuration compileClasspath --format xml > /tmp/deps.xml

# Playbook: extract current versions, apply bump rules, rewrite gradle.properties
ansible-playbook bump_versions.yml
```

The collection does not mutate `.gradle` / `.gradle.kts` files directly
(they are not XML) — it works with Gradle's XML export format and the
key-value `gradle.properties` file.

### 2.10 plist — Apple Property List

| Property | Value |
|----------|-------|
| **Schema** | Apple Property List DTD (XML plist) / binary plist format |
| **Namespace** | `http://www.apple.com/DTDs/PropertyList-1.0.dtd` |
| **Primary use** | macOS / iOS configuration, preferences, app metadata |
| **Backend** | `plistlib` (Python stdlib) |

Apple property lists store structured data in XML (or binary) format. The
collection handles:

- Read XML plist files: `plistlib.load()` → Python dict
- Write XML plist files: `plistlib.dump()` → `.plist` file
- Convert binary `.plist` to XML `.plist` via `plutil -convert xml1`
- Nested key access: `dict[key1][key2][key3]` traversal
- Plist merge: overlay user preferences onto system defaults
- Info.plist parsing: read `CFBundleVersion`, `CFBundleIdentifier`

**Example**: Read macOS preference value:

```yaml
- name: Read a macOS plist preference
  include_role:
    name: general_ludd.xml.xml_core
  vars:
    input_file: ~/Library/Preferences/com.example.app.plist
    xpath_query: "//key[text()='LastUpdateCheck']/following-sibling::date[1]/text()"
    operation: extract
    output_file: /tmp/last_update.txt
```

> `plistlib` is preferred for plist read/write; XPath is used only for
> read-only extraction. Writing back via `plistlib` preserves the binary/XML
> format of the original.

## Section 3: XML Concepts Reference

### 3.1 Elements, Attributes, and Text Content

Every XML document is a tree of **elements**. Each element has:

- A **tag name** (qualified or local)
- Zero or more **attributes** (key-value pairs in the opening tag)
- **Text content** (the characters between tags)
- Zero or more **child elements** (nested hierarchy)

```text
<book category="fiction" isbn="978-3-16-148410-0">  <!-- element with attributes -->
  <title>The Great Novel</title>                        <!-- element with text content -->
  <author born="1970">Jane Smith</author>               <!-- element with attribute + text -->
</book>
```

The `xml_core` role treats these as distinct XPath targets:
- `/book` — the element node itself
- `/book/@category` — the `category` attribute
- `/book/title/text()` — the text content of `title`
- `/book/author/@born` — the `born` attribute on `author`

### 3.2 Namespaces

XML namespaces prevent name collisions by qualifying element and attribute
names with a URI.

| Concept | Example | Meaning |
|---------|---------|---------|
| **Declaration** | `xmlns:dc="http://purl.org/dc/elements/1.1/"` | Binds prefix `dc` to the Dublin Core URI |
| **Default namespace** | `xmlns="http://www.w3.org/2005/Atom"` | Unprefixed elements belong to Atom |
| **Prefixed element** | `<dc:title>XML Guide</dc:title>` | `title` in the Dublin Core namespace |
| **Qualified in XPath** | `//dc:title/text()` | Query requires namespace map: `{"dc": "http://purl.org/dc/elements/1.1/"}` |

The collection requires explicit namespace maps for XPath queries against
namespace-qualified documents:

```yaml
namespaces:
  oai: "http://www.openarchives.org/OAI/2.0/"
  dc: "http://purl.org/dc/elements/1.1/"
  xsi: "http://www.w3.org/2001/XMLSchema-instance"
```

### 3.3 XPath Axes, Functions, and Predicates

XPath is the query language for XML. Supported feature level depends on the
backend.

| Category | Feature | ElementTree | lxml |
|----------|---------|:-----------:|:----:|
| **Axes** | `child::` (default), `attribute::` | Yes | Yes |
| | `descendant::` (`//`), `self::` | Yes | Yes |
| | `parent::` (`..`), `ancestor::`, `following-sibling::` | No | Yes |
| **Functions** | `text()`, `contains()`, `starts-with()` | Partial | Yes |
| | `count()`, `sum()`, `concat()`, `string-length()` | No | Yes |
| | `normalize-space()`, `translate()`, `substring()` | No | Yes |
| **Predicates** | `[1]` (position), `[@attr]` (has attribute) | Yes | Yes |
| | `[@attr='value']` (attribute equals) | Yes | Yes |
| | `[contains(text(), 'foo')]` (text contains) | No | Yes |
| | `[position() > 1]` (advanced position) | No | Yes |

**Common XPath patterns**:

| Goal | XPath |
|------|-------|
| All elements named `item` | `//item` |
| Third `item` child | `/root/item[3]` |
| `item` whose `id` attribute is `42` | `//item[@id='42']` |
| Text of all `title` children | `/book/title/text()` |
| `item` containing text "urgent" | `//item[contains(text(), 'urgent')]` |
| Parent of a matched element | `//item[@id='42']/..` |

### 3.4 XSD Types

XSD provides a type system for XML documents.

| Construct | Example | Purpose |
|-----------|---------|---------|
| **simpleType** | `<xs:simpleType name="Color"><xs:restriction base="xs:string"><xs:enumeration value="red"/>...` | Constrain a string to an enumerated set |
| **complexType** | `<xs:complexType name="Address"><xs:sequence><xs:element name="street"...` | Define an element with child structure |
| **restriction** | `<xs:restriction base="xs:integer"><xs:minInclusive value="0"/>...` | Narrow a base type with constraints |
| **extension** | `<xs:extension base="tns:BaseType"><xs:sequence>...` | Add children/attributes to a base type |
| **Built-in types** | `xs:string`, `xs:integer`, `xs:dateTime`, `xs:boolean`, `xs:decimal` | Primitive type vocabulary |

### 3.5 XSLT Templates, Modes, and Parameters

| Concept | XSLT Syntax | Purpose |
|---------|------------|---------|
| **Template match** | `<xsl:template match="chapter">` | Fires when processor encounters a `chapter` element |
| **Apply templates** | `<xsl:apply-templates select="section"/>` | Process children, delegating to matching templates |
| **Mode** | `<xsl:template match="title" mode="toc">` | Same element, different processing context (e.g. TOC vs body) |
| **Parameter** | `<xsl:param name="title" select="'Default'"/>` | Runtime value passed from the invoker |
| **Value-of** | `<xsl:value-of select="title"/>` | Extract text value of the selected node |
| **For-each** | `<xsl:for-each select="//item">` | Iterate over a node set |
| **Conditional** | `<xsl:if test="@priority='high'">` / `<xsl:choose>` | Branch on XPath boolean |

## Section 4: Role Reference

### 4.1 `xml_core` — XML Parsing and XPath

**FQCN**: `general_ludd.xml.xml_core`

**Purpose**: Parse XML files, execute XPath queries, manage namespace maps,
extract or modify elements, validate well-formedness.

**Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `input_file` | path | (required) | XML file to process |
| `xpath_query` | string | (required for extract/modify) | XPath 1.0 expression |
| `namespaces` | dict | `{}` | Prefix-to-URI map for namespace-qualified queries |
| `operation` | string | `extract` | One of `extract`, `modify`, `validate` |
| `output_file` | path | `/tmp/gludd-xml-core/output.xml` | Where to write results |
| `artifact_dir` | path | `/tmp/gludd-xml-core` | Working directory for temporary files |
| `python_interpreter` | string | `/usr/bin/env python3` | Python binary for inline execution |

**Operations**:

| Operation | Description | Output |
|-----------|-------------|--------|
| `extract` | Run `xpath_query`, write matching text/attributes | Line-delimited values in `output_file` |
| `modify` | Run `xpath_query`, modify matched elements (set text, set attribute, remove) | Modified XML written to `output_file` |
| `validate` | Check well-formedness; optionally XSD-validate | Pass/fail in playbook output; errors in stderr |

### 4.2 `xsd_generator` — Schema Inference

**FQCN**: `general_ludd.xml.xsd_generator`

**Purpose**: Infer an XSD schema from one or more XML instance documents.
Generates `.xsd` output with namespace support, element cardinality, and type
detection.

**Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `input_files` | list[path] | (required) | One or more XML instance documents |
| `output_file` | path | (required) | Path for the generated `.xsd` file |
| `target_namespace` | string | inferred from instances | Target namespace URI for the generated schema |
| `element_form_default` | string | `qualified` | `qualified` or `unqualified` |
| `attribute_form_default` | string | `unqualified` | `qualified` or `unqualified` |

### 4.3 `xslt_transformer` — XSLT Application

**FQCN**: `general_ludd.xml.xslt_transformer`

**Purpose**: Apply an XSLT stylesheet to an XML document, optionally chaining
multiple transformations. Supports HTML, XML, and text output formats.

**Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `input_file` | path | (required) | XML document to transform |
| `stylesheet` | path | (required) | XSLT stylesheet file |
| `output_file` | path | (required) | Where to write the transformation result |
| `output_format` | string | `xml` | `xml`, `html`, or `text` |
| `params` | dict | `{}` | Key-value parameters passed to `xsl:param` declarations |
| `chain` | list | `[]` | Optional list of additional `{stylesheet, params}` steps to chain |

## Section 5: Common Workflows

### 5.1 Extract Data from XML Config Files

Parse a server configuration file and extract specific values for use in
Ansible facts or templated files.

```yaml
- name: Extract database configuration from server.xml
  include_role:
    name: general_ludd.xml.xml_core
  vars:
    input_file: /opt/tomcat/conf/server.xml
    xpath_query: "//Resource[@name='jdbc/MyDB']/@*"
    operation: extract
  register: db_config_xml

- name: Parse extracted attributes into facts
  set_fact:
    db_url: "{{ db_config_xml.stdout_lines | select('match', '^url=') | first }}"
    db_user: "{{ db_config_xml.stdout_lines | select('match', '^username=') | first }}"
```

### 5.2 Validate XML Against XSD Before Deployment

Pre-flight validation that catches schema violations before pushing config
files to production.

```yaml
- name: Generate XSD from golden config
  include_role:
    name: general_ludd.xml.xsd_generator
  vars:
    input_files:
      - /configs/golden/app_config.xml
    target_namespace: "urn:example:app:v1"
    output_file: /tmp/app_config.xsd

- name: Validate proposed config against schema
  include_role:
    name: general_ludd.xml.xml_core
  vars:
    input_file: /configs/staging/app_config.xml
    operation: validate
    schema_file: /tmp/app_config.xsd
```

### 5.3 Transform SOAP Responses to JSON

Extract the SOAP body, strip the envelope, and convert the XML payload
to JSON for REST API consumption.

```yaml
- name: Extract SOAP body
  include_role:
    name: general_ludd.xml.xml_core
  vars:
    input_file: /data/legacy_soap_response.xml
    xpath_query: "//soap:Body/*"
    namespaces:
      soap: "http://schemas.xmlsoap.org/soap/envelope/"
    operation: extract
    output_file: /tmp/body.xml

- name: Transform XML payload to JSON
  include_role:
    name: general_ludd.xml.xslt_transformer
  vars:
    input_file: /tmp/body.xml
    stylesheet: /etc/xslt/xml_to_json.xsl
    output_format: text
    output_file: /tmp/response.json
```

### 5.4 Parse SAML Assertions for SSO Integration

Read user identity attributes from a SAML response and set Ansible facts
for downstream authentication.

```yaml
- name: Extract NameID from SAML assertion
  include_role:
    name: general_ludd.xml.xml_core
  vars:
    input_file: /tmp/saml_response.xml
    xpath_query: "//saml2:Subject/saml2:NameID/text()"
    namespaces:
      saml2: "urn:oasis:names:tc:SAML:2.0:assertion"
    operation: extract
  register: saml_nameid

- name: Extract all attributes
  include_role:
    name: general_ludd.xml.xml_core
  vars:
    input_file: /tmp/saml_response.xml
    xpath_query: "//saml2:Attribute[@Name]/@Name"
    namespaces:
      saml2: "urn:oasis:names:tc:SAML:2.0:assertion"
    operation: extract
  register: saml_attrs

- name: Set user identity facts
  set_fact:
    sso_user_id: "{{ saml_nameid.stdout }}"
    sso_attributes: "{{ saml_attrs.stdout_lines }}"
```

### 5.5 Convert DocBook to HTML Documentation

A full documentation build pipeline: validate → transform → deploy.

```yaml
- name: Validate DocBook source
  include_role:
    name: general_ludd.xml.xml_core
  vars:
    input_file: /docs/src/book.xml
    operation: validate

- name: Generate HTML documentation
  include_role:
    name: general_ludd.xml.xslt_transformer
  vars:
    input_file: /docs/src/book.xml
    stylesheet: /usr/share/xml/docbook/xsl-stylesheets/xhtml/chunk.xsl
    output_format: html
    params:
      base.dir: "/tmp/docs/html/"
      chunk.section.depth: "2"
      generate.toc: "book toc"
  register: doc_output

- name: Deploy HTML to web server
  copy:
    src: /tmp/docs/html/
    dest: /var/www/docs/
    mode: "0755"
```

### 5.6 Update Gradle Dependency Versions Programmatically

Export Gradle's resolved dependency graph as XML, audit for outdated
versions, and write updated properties.

```yaml
- name: Export Gradle dependencies as XML
  command: >
    gradle dependencies --configuration runtimeClasspath
    --format xml > /tmp/gradle_deps.xml
  args:
    chdir: /opt/project

- name: Extract dependency versions from XML export
  include_role:
    name: general_ludd.xml.xml_core
  vars:
    input_file: /tmp/gradle_deps.xml
    xpath_query: "//dependency[@group='com.example']"
    operation: extract
  register: dep_versions

- name: Update gradle.properties with new versions
  lineinfile:
    path: /opt/project/gradle.properties
    regexp: "^com_example_version="
    line: "com_example_version=2.5.0"
```

### 5.7 Read / Write macOS plist Preferences

Read a plist file, modify a specific key, and write it back.

```python
# Inline Python task using plistlib (stdlib)
- name: Read and modify macOS plist
  ansible.builtin.script:
    cmd: |
      import plistlib, sys
      with open(sys.argv[1], 'rb') as f:
          data = plistlib.load(f)
      data['LastUpdateCheck'] = '2026-07-12T00:00:00Z'
      with open(sys.argv[1], 'wb') as f:
          plistlib.dump(data, f)
    executable: "{{ python_interpreter }}"
  args:
    chdir: ~/Library/Preferences/
  vars:
    plist_file: com.example.app.plist
```

For read-only extraction, use XPath via `xml_core`:

```yaml
- name: Extract key from XML plist
  include_role:
    name: general_ludd.xml.xml_core
  vars:
    input_file: "~/Library/Preferences/com.example.app.plist"
    xpath_query: "//key[text()='LastUpdateCheck']/following-sibling::string[1]/text()"
    operation: extract
```

## Section 6: Tool Matrix

Which Python libraries are used by which role or operation.

| Role / Operation | `xml.etree.ElementTree` | `lxml` | `plistlib` | `xmlsec` / `signxml` |
|------------------|:-----------------------:|:------:|:----------:|:---------------------:|
| `xml_core` — extract (basic XPath) | Required | — | — | — |
| `xml_core` — extract (rich XPath) | Fallback | Required | — | — |
| `xml_core` — modify | Required | Required | — | — |
| `xml_core` — validate (well-formed) | Required | — | — | — |
| `xml_core` — validate (XSD) | — | Required | — | — |
| `xsd_generator` — infer schema | — | Required | — | — |
| `xslt_transformer` — XSLT 1.0 | — | Required | — | — |
| `xslt_transformer` — XSLT 2.0/3.0 | — | — | — | — (uses Saxon CLI) |
| plist read (XML) | — | — | Required | — |
| plist read (binary) | — | — | Required | — |
| SAML signature verification | — | Required | — | Required |
| HTML tag-soup parsing | — | `lxml.html` | — | — |

### Library roles

| Library | Role | Installation |
|---------|------|-------------|
| `xml.etree.ElementTree` | Fast, stdlib-only XML parsing and basic XPath. Used for simple extract/validate operations | Built into Python (no install needed) |
| `lxml` | Full XPath 1.0, XSD validation, XSLT 1.0, HTML parser. Required for all advanced operations | `pip install lxml` or `apt install python3-lxml` |
| `plistlib` | Apple Property List read/write (XML and binary formats) | Built into Python (no install needed) |
| `xmlsec` | XML Digital Signature verification (SAML, WS-Security) | `pip install xmlsec` (requires libxmlsec1 system library) |
| `signxml` | Higher-level XML signature API on top of `xmlsec` | `pip install signxml` |

### Dependencies

```yaml
# galaxy.yml dependencies
dependencies: {}
```

The collection has no Ansible collection dependencies, but requires these
Python packages at runtime:

```text
xml.etree.ElementTree  # stdlib — always available
lxml                   # optional — enables XSD, XSLT, rich XPath
plistlib               # stdlib — macOS plist support
```

## Section 7: Error Handling and Observability

### Well-formedness errors

When `xml_core` encounters a malformed XML document, the error is surfaced
with the parser's line number and column:

```text
xml.etree.ElementTree.ParseError: not well-formed (invalid token): line 42, column 17
```

The playbook fails with `failed=True` and the parse error in `msg`.

### XPath mismatch

When an XPath query returns no results, the role writes an empty file and
issues a warning — it does not fail the playbook by default. Set
`fail_on_empty: true` (future parameter) to hard-fail on no results.

### Namespace misconfiguration

If a namespace prefix in the XPath query is not declared in the
`namespaces` dict, `lxml` raises:

```text
lxml.etree.XPathEvalError: Undefined namespace prefix
```

The collection validates that every prefix in the query has a corresponding
entry in `namespaces` before executing.

### XSLT errors

Stylesheet compilation errors surface as `lxml.etree.XSLTParseError` with
the error line. Runtime XSLT errors (e.g., calling an undefined function)
surface as `lxml.etree.XSLTApplyError` and include the context node.

## Section 8: Limitations and Future Work

### Current limitations

1. **XSLT 1.0 only via lxml.** XSLT 2.0/3.0 require Saxon (Java) as an
   external process; the collection does not bundle or manage Saxon.
2. **No streaming parser.** All documents are loaded into memory via
   `ElementTree.parse()` or `lxml.etree.parse()`. Documents larger than
   available RAM require an external streaming solution.
3. **Schema validation is read-only.** `xml_core` can validate against an
   XSD but cannot generate compliance reports or suggest fixes.
4. **Gradle build files are not native XML.** The collection works with
   Gradle's XML export format; it cannot parse `.gradle` or `.gradle.kts`
   files directly.
5. **No XML Digital Signature verification.** SAML assertion signatures
   need external `xmlsec`/`signxml` — the collection does not bundle or
   invoke signature verification.

### Future roles (planned)

| Role | Purpose |
|------|---------|
| `soap_client` | Construct and send SOAP requests, parse responses |
| `saml_toolkit` | Verify signatures, validate conditions, build assertions |
| `html_parser` | Tag-soup tolerant HTML extraction via `lxml.html` |
| `docbook_publisher` | End-to-end DocBook → HTML/PDF pipeline with DI |
| `dita_processor` | DITA-OT integration for topic-based publishing |
| `gradle_version_auditor` | Dependency version audit via Gradle XML export |
| `plist_manager` | macOS/iOS plist read/write/merge with `plistlib` |

## Section 9: Quick Reference

### Install the collection

```bash
ansible-galaxy collection install git+https://github.com/anomalyco/gludd.git#collections/ansible_collections/general_ludd/xml
```

### Minimal playbook

```yaml
---
- hosts: localhost
  tasks:
    - name: Parse XML and extract values
      include_role:
        name: general_ludd.xml.xml_core
      vars:
        input_file: /path/to/file.xml
        xpath_query: "//element/@attribute"
        namespaces:
          ns: "http://example.com/ns"
        operation: extract
        output_file: /tmp/result.txt
```

### One-liner fact extraction

```yaml
- name: Get XML value as Ansible fact
  include_role: {name: general_ludd.xml.xml_core}
  vars:
    input_file: /etc/config.xml
    xpath_query: "//setting[@key='timeout']/text()"
    operation: extract
  register: xml_result

- set_fact:
    timeout: "{{ xml_result.stdout | trim }}"
```

### Gate checks

| Check | Command | Expectation |
|-------|---------|-------------|
| Collection structure | `ansible-galaxy collection list general_ludd.xml` | Listed with version |
| Role validation | `ansible-playbook --syntax-check playbook.yml` | No errors |
| Role listing | `ansible-doc --list general_ludd.xml` | 3 roles listed |
| XSD generation | Run playbook, check `output_file` is valid XSD | Validates against XSD schema for schemas |
| XSLT output | Run playbook, parse `output_file` with target format validator | Valid HTML/XML/text |
