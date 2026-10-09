# `general_ludd.xml.saml_processor`

Admit one SAML 2.0 assertion through the collection-native controller action.
The role cryptographically verifies the assertion with `signxml==5.1.0` and an
explicit trusted certificate, then exposes only bounded allowlisted claims from
SignXML's verified subtree. It never runs inline Python, creates a temporary
file, downloads metadata, or reports an unverified signature as valid.

## Inputs

Use exactly one source:

- `saml_processor_xml`: a no-log inline Response or Assertion up to 1 MiB; or
- `saml_processor_root`, `saml_processor_path`, and
  `saml_processor_sha256`: one root-confined, stable, regular, single-link file.

The following bindings are mandatory:

- `saml_processor_trusted_certificate`
- `saml_processor_expected_issuer`
- `saml_processor_expected_audience`
- `saml_processor_expected_destination`
- `saml_processor_expected_in_response_to`
- `saml_processor_allowed_claims`

`saml_processor_clock_skew_seconds` defaults to 30 and cannot exceed 120.
The task is `no_log`; the registered `saml_processor_result` remains available
to subsequent tasks without printing the raw XML, certificate, or claims.

```yaml
- name: Admit the received response
  ansible.builtin.include_role:
    name: general_ludd.xml.saml_processor
  vars:
    saml_processor_root: /srv/gludd/saml
    saml_processor_path: response.xml
    saml_processor_sha256: "{{ admitted_response_sha256 }}"
    saml_processor_trusted_certificate: "{{ idp_certificate_pem }}"
    saml_processor_expected_issuer: https://idp.example.test
    saml_processor_expected_audience: https://sp.example.test
    saml_processor_expected_destination: https://sp.example.test/acs
    saml_processor_expected_in_response_to: request-1
    saml_processor_allowed_claims: [role, groups]
```

## Delivery and rollback

Build the digest-addressed controller execution environment, run signed
validation canaries for every identity-provider contract, and shift new jobs to
the candidate image. Existing jobs drain on the old image; this pure action has
no listener, worker, cache, schema, or persistent state to hand off. Rollback
routes new jobs to the prior image and pinned certificate. A failed canary is a
closed admission, never a reason to enable the legacy structural signature
check. This is zero-downtime delivery because both immutable images can process
independent bounded tasks during the shift.

The full security rationale, practitioner evidence from python3-saml
[issue #282](https://github.com/SAML-Toolkits/python3-saml/issues/282) and
[issue #39](https://github.com/SAML-Toolkits/python3-saml/issues/39), ceilings,
and verification matrix are in
[`NATIVE_SAML_ASSERTION_ADMISSION.md`](../../../../../../docs/features/NATIVE_SAML_ASSERTION_ADMISSION.md).
