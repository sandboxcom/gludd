# `general_ludd.xml.saml_processor` — SAML 2.0 Assertion Processing

Parse and inspect SAML 2.0 documents: AuthnRequest, Response, and Assertion. Extract attributes, conditions, NameID, and detect XMLDSig signatures.

## Quick start

```yaml
- name: Parse SAML Response
  hosts: localhost
  vars:
    saml_processor_file: "/tmp/saml_response.xml"
    saml_processor_extract_attributes: true
  roles:
    - general_ludd.xml.saml_processor
```

## Parameters

| Variable | Default | Description |
|---|---|---|
| `saml_processor_file` | `""` | Path to SAML XML file |
| `saml_processor_xml` | `""` | Inline SAML XML string |
| `saml_processor_validate_signature` | `false` | Check XMLDSig signature structure |
| `saml_processor_extract_attributes` | `true` | Extract AttributeStatement values |
| `saml_processor_issuer` | `""` | Filter results by Issuer |

## Results

```python
{
    "type": "Response",
    "id": "_abc123",
    "status": "urn:oasis:names:tc:SAML:2.0:status:Success",
    "issuer": "https://idp.example.com",
    "nameid": {"value": "user@example.com", "format": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"},
    "conditions": {"not_before": "2024-01-01T00:00:00Z", "not_on_or_after": "2024-01-01T01:00:00Z"},
    "audience": ["https://sp.example.com"],
    "attributes": {"uid": "jdoe", "groups": ["admin", "user"]},
    "signature_present": true,
    "signature_algorithm": "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
    "signature_valid": "unverified"
}
```
