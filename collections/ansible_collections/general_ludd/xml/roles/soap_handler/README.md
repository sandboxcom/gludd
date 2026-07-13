# `general_ludd.xml.soap_handler` — SOAP Messaging

Construct and parse SOAP envelopes with namespace-correct headers and body extraction. Supports SOAP 1.1, 1.2, and WS-Security headers.

## Quick start

```yaml
- name: Build a SOAP envelope
  hosts: localhost
  vars:
    soap_handler_operation_name: "GetStockPrice"
    soap_handler_namespace: "http://example.com/stock"
    soap_handler_parameters:
      ticker: "AAPL"
    soap_handler_soap_version: "1.1"
  roles:
    - general_ludd.xml.soap_handler
```

## Modes

| Variable | Mode | Description |
|---|---|---|
| `soap_handler_parse_mode: false` | **Build** | Construct a SOAP envelope from parameters |
| `soap_handler_parse_mode: true` | **Parse** | Parse a SOAP response/envelope for body/fault |

## Build mode parameters

| Variable | Default | Description |
|---|---|---|
| `soap_handler_operation_name` | `""` | SOAP operation to invoke |
| `soap_handler_namespace` | `""` | XML namespace for the operation |
| `soap_handler_parameters` | `{}` | Key-value parameters for the operation |
| `soap_handler_soap_version` | `"1.1"` | `"1.1"` or `"1.2"` |
| `soap_handler_endpoint_url` | `""` | Target endpoint URL |
| `soap_handler_soap_action` | `""` | SOAPAction HTTP header value |
| `soap_handler_security_headers` | `""` | Raw XML for WS-Security header |

## Parse mode parameters

| Variable | Default | Description |
|---|---|---|
| `soap_handler_raw_envelope` | `""` | Raw SOAP XML or path to XML file |

## Results

```python
# Build mode
{"mode": "build", "soap_version": "1.1", "envelope": "<soap:Envelope...", "endpoint": "...", "soap_action": "..."}

# Parse mode
{"mode": "parse", "soap_version": "1.1", "body": "<soap:Body>...", "fault": null, "has_fault": false}
```
