#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: saml_processor
  short_description: Admit one pinned, signature-verified SAML assertion
  description:
    - Runs only through the controller-side action plugin.
    - Verifies one bounded Response or Assertion with SignXML and an explicit certificate.
    - Returns only bounded allowlisted claims from SignXML's verified subtree.
  options:
    root:
      type: path
      default: ''
    saml_path:
      type: path
      default: ''
    saml_sha256:
      type: str
      default: ''
    saml_xml:
      type: str
      default: ''
    trusted_certificate:
      type: str
      required: true
    expected_issuer:
      type: str
      required: true
    expected_audience:
      type: str
      required: true
    expected_destination:
      type: str
      required: true
    expected_in_response_to:
      type: str
      required: true
    allowed_claims:
      type: list
      elements: str
      required: true
    clock_skew_seconds:
      type: int
      default: 30

EXAMPLES:
  - name: Admit a locally received SAML response
    general_ludd.xml.saml_processor:
      root: /srv/gludd/saml
      saml_path: response.xml
      saml_sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
      trusted_certificate: "{{ lookup('file', '/run/trust/idp.pem') }}"
      expected_issuer: https://idp.example.test
      expected_audience: https://sp.example.test
      expected_destination: https://sp.example.test/acs
      expected_in_response_to: request-1
      allowed_claims: [role, groups]

RETURN:
  admitted:
    type: bool
    returned: always
  saml_sha256:
    type: str
    returned: successful admission
  claims:
    type: dict
    returned: successful admission
"""

from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule

ARGUMENT_SPEC = {
    "root": {"type": "path", "default": ""},
    "saml_path": {"type": "path", "default": ""},
    "saml_sha256": {"type": "str", "default": ""},
    "saml_xml": {"type": "str", "default": "", "no_log": True},
    "trusted_certificate": {"type": "str", "required": True, "no_log": True},
    "expected_issuer": {"type": "str", "required": True},
    "expected_audience": {"type": "str", "required": True},
    "expected_destination": {"type": "str", "required": True},
    "expected_in_response_to": {"type": "str", "required": True},
    "allowed_claims": {"type": "list", "elements": "str", "required": True},
    "clock_skew_seconds": {"type": "int", "default": 30},
}


def main() -> None:
    """Fail closed if Ansible bypasses the controller-side action plugin."""
    module = AnsibleModule(
        argument_spec=ARGUMENT_SPEC,
        mutually_exclusive=(("saml_xml", "saml_path"),),
        supports_check_mode=True,
    )
    module.fail_json(msg="saml_processor requires its controller-side action plugin")


if __name__ == "__main__":
    main()
