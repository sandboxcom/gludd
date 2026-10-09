"""Fail-closed collection-native SAML assertion admission contracts."""

from __future__ import annotations

import hashlib
import runpy
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree
from scripts.check_collection_python_boundary import scan_collections
from signxml import XMLSigner
from signxml.algorithms import SignatureConstructionMethod

ROOT = Path(__file__).resolve().parents[2]
COLLECTIONS = ROOT / "collections"
if str(COLLECTIONS) not in sys.path:
    sys.path.insert(0, str(COLLECTIONS))

from ansible_collections.general_ludd.xml.plugins.action import (  # noqa: E402
    saml_processor as action_plugin,
)
from ansible_collections.general_ludd.xml.plugins.action.saml_processor import (  # noqa: E402
    ActionModule,
    execute_action,
)
from ansible_collections.general_ludd.xml.plugins.module_utils import (  # noqa: E402
    saml_admission,
)
from ansible_collections.general_ludd.xml.plugins.modules import (  # noqa: E402
    saml_processor as module_stub,
)

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
SAML = "urn:oasis:names:tc:SAML:2.0:assertion"
SAMLP = "urn:oasis:names:tc:SAML:2.0:protocol"
DS = "http://www.w3.org/2000/09/xmldsig#"


def _assertion(*, subject: str = "verified@example.test") -> etree._Element:
    assertion = etree.Element(
        f"{{{SAML}}}Assertion",
        nsmap={"saml": SAML, "ds": DS},
        ID="assertion-1",
        Version="2.0",
        IssueInstant=NOW.isoformat().replace("+00:00", "Z"),
    )
    etree.SubElement(assertion, f"{{{SAML}}}Issuer").text = "https://idp.example.test"
    signature = etree.SubElement(assertion, f"{{{DS}}}Signature")
    signed_info = etree.SubElement(signature, f"{{{DS}}}SignedInfo")
    etree.SubElement(
        signed_info,
        f"{{{DS}}}CanonicalizationMethod",
        Algorithm="http://www.w3.org/2006/12/xml-c14n11",
    )
    etree.SubElement(
        signed_info,
        f"{{{DS}}}SignatureMethod",
        Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
    )
    reference = etree.SubElement(signed_info, f"{{{DS}}}Reference", URI="#assertion-1")
    etree.SubElement(
        reference,
        f"{{{DS}}}DigestMethod",
        Algorithm="http://www.w3.org/2001/04/xmlenc#sha256",
    )
    etree.SubElement(reference, f"{{{DS}}}DigestValue").text = "placeholder"
    etree.SubElement(signature, f"{{{DS}}}SignatureValue").text = "placeholder"
    subject_node = etree.SubElement(assertion, f"{{{SAML}}}Subject")
    etree.SubElement(subject_node, f"{{{SAML}}}NameID").text = subject
    confirmation = etree.SubElement(
        subject_node,
        f"{{{SAML}}}SubjectConfirmation",
        Method="urn:oasis:names:tc:SAML:2.0:cm:bearer",
    )
    etree.SubElement(
        confirmation,
        f"{{{SAML}}}SubjectConfirmationData",
        Recipient="https://sp.example.test/acs",
        InResponseTo="request-1",
        NotOnOrAfter=(NOW + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
    )
    conditions = etree.SubElement(
        assertion,
        f"{{{SAML}}}Conditions",
        NotBefore=(NOW - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        NotOnOrAfter=(NOW + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
    )
    restriction = etree.SubElement(conditions, f"{{{SAML}}}AudienceRestriction")
    etree.SubElement(restriction, f"{{{SAML}}}Audience").text = "https://sp.example.test"
    statement = etree.SubElement(assertion, f"{{{SAML}}}AttributeStatement")
    role = etree.SubElement(statement, f"{{{SAML}}}Attribute", Name="role")
    etree.SubElement(role, f"{{{SAML}}}AttributeValue").text = "operator"
    return assertion


def _response(assertion: etree._Element) -> bytes:
    response = etree.Element(
        f"{{{SAMLP}}}Response",
        nsmap={"samlp": SAMLP, "saml": SAML, "ds": DS},
        ID="response-1",
        Version="2.0",
        IssueInstant=NOW.isoformat().replace("+00:00", "Z"),
        Destination="https://sp.example.test/acs",
        InResponseTo="request-1",
    )
    etree.SubElement(response, f"{{{SAML}}}Issuer").text = "https://idp.example.test"
    status = etree.SubElement(response, f"{{{SAMLP}}}Status")
    etree.SubElement(
        status,
        f"{{{SAMLP}}}StatusCode",
        Value="urn:oasis:names:tc:SAML:2.0:status:Success",
    )
    response.append(assertion)
    return etree.tostring(response)


def _arguments(xml: bytes) -> dict[str, Any]:
    return {
        "saml_xml": xml.decode(),
        "trusted_certificate": "-----BEGIN CERTIFICATE-----\nfixture\n-----END CERTIFICATE-----",
        "expected_issuer": "https://idp.example.test",
        "expected_audience": "https://sp.example.test",
        "expected_destination": "https://sp.example.test/acs",
        "expected_in_response_to": "request-1",
        "allowed_claims": ["role"],
        "clock_skew_seconds": 30,
    }


class _Verified:
    def __init__(self, assertion: object) -> None:
        self.assertion = assertion

    def __call__(self) -> _Verified:
        return self

    def verify(self, *_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(signed_xml=self.assertion)


def _admit(
    raw: bytes,
    verified: etree._Element | None = None,
    **overrides: object,
) -> dict[str, object]:
    arguments = {**_arguments(raw), **overrides}
    return saml_admission.admit_saml_assertion(
        **arguments,
        verifier_factory=_Verified(verified if verified is not None else _assertion()),
        now=lambda: NOW,
    )


def _real_signed_response() -> tuple[bytes, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "idp.example.test")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    cert_pem = certificate.public_bytes(serialization.Encoding.PEM).decode()
    assertion = _assertion()
    assertion.remove(assertion.find(f"{{{DS}}}Signature"))
    signed = XMLSigner(
        method=SignatureConstructionMethod.enveloped,
        signature_algorithm="rsa-sha256",
        digest_algorithm="sha256",
        c14n_algorithm="http://www.w3.org/2001/10/xml-exc-c14n#",
    ).sign(
        assertion,
        key=key_pem,
        cert=cert_pem,
        reference_uri="#assertion-1",
        id_attribute="ID",
    )
    return _response(signed), cert_pem


def test_native_saml_processor_consumes_only_verified_signed_assertion() -> None:
    raw = _response(_assertion(subject="unsigned-attacker@example.test"))
    verified = _assertion(subject="verified@example.test")

    class Verifier:
        def verify(self, *_args: object, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(signed_xml=verified)

    result = saml_admission.admit_saml_assertion(
        **_arguments(raw),
        verifier_factory=Verifier,
        now=lambda: NOW,
    )

    assert result["subject"] == "verified@example.test"
    assert "unsigned-attacker@example.test" not in str(result)
    assert result["claims"] == {"role": ["operator"]}
    assert result["changed"] is False
    assert result["admitted"] is True


def test_real_signxml_signature_is_verified_with_explicit_certificate() -> None:
    xml, certificate = _real_signed_response()
    result = saml_admission.admit_saml_assertion(
        **{**_arguments(xml), "trusted_certificate": certificate},
        now=lambda: NOW,
    )

    assert result["admitted"] is True
    assert result["assertion_id"] == "assertion-1"
    assert result["saml_sha256"] == hashlib.sha256(xml).hexdigest()
    assert result["claims"] == {"role": ["operator"]}
    assert "CERTIFICATE" not in str(result)
    assert xml.decode() not in str(result)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("duplicate_assertion", "exactly one Assertion"),
        ("duplicate_id", "unique"),
        ("encrypted", "encrypted assertions"),
        ("sha1", "SHA-2"),
        ("external_reference", "reference only the Assertion ID"),
        ("nested_signature", "exactly one direct XML signature"),
    ],
)
def test_wrapping_encryption_and_weak_or_external_signatures_fail_closed(
    mutation: str,
    message: str,
) -> None:
    assertion = _assertion()
    response = etree.fromstring(_response(assertion))
    assertion = response.find(f"{{{SAML}}}Assertion")
    assert assertion is not None
    if mutation == "duplicate_assertion":
        extra = _assertion(subject="attacker@example.test")
        extra.set("ID", "assertion-2")
        extra.xpath("./ds:Signature/ds:SignedInfo/ds:Reference", namespaces={"ds": DS})[0].set(
            "URI", "#assertion-2"
        )
        response.append(extra)
    elif mutation == "duplicate_id":
        response.set("ID", "assertion-1")
    elif mutation == "encrypted":
        etree.SubElement(response, f"{{{SAML}}}EncryptedAssertion")
    elif mutation == "sha1":
        assertion.xpath("./ds:Signature/ds:SignedInfo/ds:SignatureMethod", namespaces={"ds": DS})[0].set(
            "Algorithm", "http://www.w3.org/2000/09/xmldsig#rsa-sha1"
        )
    elif mutation == "external_reference":
        assertion.xpath("./ds:Signature/ds:SignedInfo/ds:Reference", namespaces={"ds": DS})[0].set(
            "URI", "https://evil.example.test/assertion"
        )
    else:
        etree.SubElement(assertion.find(f"{{{SAML}}}Subject"), f"{{{DS}}}Signature")

    with pytest.raises(saml_admission.SAMLAdmissionError, match=message):
        _admit(etree.tostring(response), assertion)


@pytest.mark.parametrize(
    "payload",
    [
        b'<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]><x>&secret;</x>',
        b'<?xml-stylesheet href="https://evil.example.test/x.xsl"?><x/>',
        b'<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform"/>',
    ],
)
def test_dtd_entities_processing_instructions_and_xslt_are_rejected(payload: bytes) -> None:
    with pytest.raises(saml_admission.SAMLAdmissionError, match=r"DTD|processing|XSLT"):
        _admit(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("expected_issuer", "https://other.example.test", "Response issuer"),
        ("expected_audience", "https://other.example.test", "audience"),
        ("expected_destination", "https://sp.example.test/other", "destination"),
        ("expected_in_response_to", "request-other", "InResponseTo"),
    ],
)
def test_response_and_verified_assertion_are_bound_to_expected_context(
    field: str,
    value: str,
    message: str,
) -> None:
    raw = _response(_assertion())
    with pytest.raises(saml_admission.SAMLAdmissionError, match=message):
        _admit(raw, **{field: value})


def test_verified_subtree_must_match_issuer_audience_recipient_request_and_time() -> None:
    raw = _response(_assertion())
    mutations: list[tuple[etree._Element, str]] = []

    issuer = _assertion()
    issuer.find(f"{{{SAML}}}Issuer").text = "https://evil.example.test"
    mutations.append((issuer, "issuer"))

    audience = _assertion()
    audience.find(f".//{{{SAML}}}Audience").text = "https://evil.example.test"
    mutations.append((audience, "audience"))

    recipient = _assertion()
    recipient.find(f".//{{{SAML}}}SubjectConfirmationData").set(
        "Recipient", "https://evil.example.test/acs"
    )
    mutations.append((recipient, "recipient"))

    request = _assertion()
    request.find(f".//{{{SAML}}}SubjectConfirmationData").set("InResponseTo", "other")
    mutations.append((request, "InResponseTo"))

    expired = _assertion()
    expired.find(f"{{{SAML}}}Conditions").set(
        "NotOnOrAfter", (NOW - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    )
    mutations.append((expired, "expired"))

    future = _assertion()
    future.set("IssueInstant", (NOW + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"))
    mutations.append((future, "future"))

    for assertion, message in mutations:
        with pytest.raises(saml_admission.SAMLAdmissionError, match=message):
            _admit(raw, assertion)


def test_claims_are_allowlisted_scalar_bounded_and_unambiguous() -> None:
    raw = _response(_assertion())
    with pytest.raises(saml_admission.SAMLAdmissionError, match="outside allowed_claims"):
        _admit(raw, allowed_claims=[])

    duplicate = _assertion()
    statement = duplicate.find(f"{{{SAML}}}AttributeStatement")
    repeated = etree.SubElement(statement, f"{{{SAML}}}Attribute", Name="role")
    etree.SubElement(repeated, f"{{{SAML}}}AttributeValue").text = "duplicate"
    with pytest.raises(saml_admission.SAMLAdmissionError, match="duplicate claim"):
        _admit(raw, duplicate)

    nested = _assertion()
    value = nested.find(f".//{{{SAML}}}AttributeValue")
    etree.SubElement(value, f"{{{SAML}}}NameID").text = "nested"
    with pytest.raises(saml_admission.SAMLAdmissionError, match="scalar text"):
        _admit(raw, nested)

    with pytest.raises(saml_admission.SAMLAdmissionError, match="32-claim"):
        _admit(raw, allowed_claims=[f"claim-{index}" for index in range(33)])


def test_file_input_is_digest_bound_confined_regular_and_single_link(tmp_path: Path) -> None:
    xml = _response(_assertion())
    source = tmp_path / "response.xml"
    source.write_bytes(xml)
    digest = hashlib.sha256(xml).hexdigest()
    file_args = {
        **_arguments(xml),
        "saml_xml": "",
        "root": str(tmp_path),
        "saml_path": "response.xml",
        "saml_sha256": digest,
    }
    result = saml_admission.admit_saml_assertion(
        **file_args,
        verifier_factory=_Verified(_assertion()),
        now=lambda: NOW,
    )
    assert result["saml_sha256"] == digest

    with pytest.raises(saml_admission.SAMLAdmissionError, match="digest"):
        saml_admission.admit_saml_assertion(
            **{**file_args, "saml_sha256": "0" * 64},
            verifier_factory=_Verified(_assertion()),
            now=lambda: NOW,
        )

    symlink = tmp_path / "linked.xml"
    symlink.symlink_to(source)
    with pytest.raises(saml_admission.SAMLAdmissionError, match="symlink"):
        saml_admission.admit_saml_assertion(
            **{**file_args, "saml_path": "linked.xml"},
            verifier_factory=_Verified(_assertion()),
            now=lambda: NOW,
        )

    hardlink = tmp_path / "hardlinked.xml"
    hardlink.hardlink_to(source)
    with pytest.raises(saml_admission.SAMLAdmissionError, match="regular unlinked"):
        saml_admission.admit_saml_assertion(
            **{**file_args, "saml_path": "hardlinked.xml"},
            verifier_factory=_Verified(_assertion()),
            now=lambda: NOW,
        )


def test_source_clock_certificate_and_argument_bounds_fail_closed(tmp_path: Path) -> None:
    raw = _response(_assertion())
    base = _arguments(raw)
    cases = [
        ({**base, "saml_xml": ""}, "exactly one"),
        ({**base, "root": str(tmp_path)}, "inline SAML"),
        ({**base, "clock_skew_seconds": 121}, "120"),
        ({**base, "clock_skew_seconds": True}, "integer"),
        ({**base, "trusted_certificate": "not a certificate"}, "PEM"),
        ({**base, "extra": "value"}, "unsupported"),
    ]
    for arguments, message in cases:
        with pytest.raises((saml_admission.SAMLAdmissionError, TypeError), match=message):
            execute_action(
                arguments,
                verifier_factory=_Verified(_assertion()),
                now=lambda: NOW,
            )


def test_check_mode_is_the_same_pure_admission_and_action_errors_are_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _response(_assertion())
    normal = execute_action(
        _arguments(raw),
        verifier_factory=_Verified(_assertion()),
        now=lambda: NOW,
    )
    checked = execute_action(
        _arguments(raw),
        check_mode=True,
        verifier_factory=_Verified(_assertion()),
        now=lambda: NOW,
    )
    assert checked == {**normal, "check_mode": True}
    assert normal["changed"] is checked["changed"] is False

    action = object.__new__(ActionModule)
    action._task = SimpleNamespace(
        args={**_arguments(raw), "trusted_certificate": "secret-cert-material"},
        check_mode=False,
    )
    monkeypatch.setattr(action_plugin._ActionBase, "run", lambda *_args, **_kwargs: {})
    result = action.run()
    assert result["failed"] is True
    assert result["changed"] is False
    assert "secret-cert-material" not in str(result)
    assert raw.decode() not in str(result)


def test_remote_module_bypass_fails_closed_and_marks_sensitive_inputs_no_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Module:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["supports_check_mode"] is True
            assert kwargs["argument_spec"]["saml_xml"]["no_log"] is True
            assert kwargs["argument_spec"]["trusted_certificate"]["no_log"] is True

        def fail_json(self, **kwargs: Any) -> None:
            raise RuntimeError(kwargs["msg"])

    monkeypatch.setattr(module_stub, "AnsibleModule", Module)
    with pytest.raises(RuntimeError, match="controller-side action plugin"):
        module_stub.main()
    monkeypatch.setattr("ansible.module_utils.basic.AnsibleModule", Module)
    with pytest.raises(RuntimeError, match="controller-side action plugin"):
        runpy.run_path(str(Path(module_stub.__file__)), run_name="__main__")


def test_role_molecule_dependency_and_no_escape_hatch_contracts() -> None:
    collection = ROOT / "collections/ansible_collections/general_ludd/xml"
    role = collection / "roles/saml_processor"
    defaults = yaml.safe_load((role / "defaults/main.yml").read_text(encoding="utf-8"))
    tasks = (role / "tasks/main.yml").read_text(encoding="utf-8")
    readme = (role / "README.md").read_text(encoding="utf-8")
    feature = (ROOT / "docs/features/NATIVE_SAML_ASSERTION_ADMISSION.md").read_text(encoding="utf-8")
    scenario = ROOT / "molecule/playbooks/xml_saml_admission"
    molecule = yaml.safe_load((scenario / "molecule.yml").read_text(encoding="utf-8"))
    verify = (scenario / "default/verify.yml").read_text(encoding="utf-8")
    requirements = (ROOT / "config/ansible/requirements.txt").read_text(encoding="utf-8")
    controller = (ROOT / "requirements/profiles/ansible-controller/pyproject.toml").read_text(
        encoding="utf-8"
    )
    source = Path(saml_admission.__file__).read_text(encoding="utf-8")

    assert defaults and all(name.startswith("saml_processor_") for name in defaults)
    assert "general_ludd.xml.saml_processor" in tasks
    for forbidden in ("command:", "shell:", "copy:", "slurp:", "tempfile:"):
        assert forbidden not in tasks
    assert molecule["provisioner"]["env"]["ANSIBLE_COLLECTIONS_SCAN_SYS_PATH"] == "false"
    assert "general_ludd.xml.saml_processor" in verify
    assert "signxml==5.1.0" in requirements
    assert "signxml==5.1.0" in controller
    assert scan_collections(collection) == []
    for phrase in ("canary", "drain", "rollback", "zero-downtime", "issue #282", "issue #39"):
        assert phrase in (readme + feature).lower()
    for forbidden in (
        "subprocess",
        "urlopen",
        "requests.",
        "socket.",
        "tempfile",
        "namedtemporaryfile",
        "thread",
    ):
        assert forbidden not in source.lower()


def test_local_file_parser_and_identifier_guard_edges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(saml_admission.SAMLAdmissionError, match="non-empty"):
        saml_admission._bounded_text(None, "value")
    with pytest.raises(saml_admission.SAMLAdmissionError, match="byte limit"):
        saml_admission._bounded_text("xx", "value", maximum=1)
    with pytest.raises(saml_admission.SAMLAdmissionError, match="absolute"):
        saml_admission._root("relative")
    with pytest.raises(saml_admission.SAMLAdmissionError, match="unavailable"):
        saml_admission._root(str(tmp_path / "missing"))

    root = tmp_path / "root"
    root.mkdir()
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(root, target_is_directory=True)
    with pytest.raises(saml_admission.SAMLAdmissionError, match="must not be a symlink"):
        saml_admission._root(str(linked_root))
    regular_root = tmp_path / "not-a-root"
    regular_root.write_text("x", encoding="utf-8")
    with pytest.raises(saml_admission.SAMLAdmissionError, match="directory"):
        saml_admission._root(str(regular_root))

    root_input, resolved = saml_admission._root(str(root))
    with pytest.raises(saml_admission.SAMLAdmissionError, match="escapes root"):
        saml_admission._confined_file(root_input, resolved, "../outside.xml")
    outside = tmp_path / "outside.xml"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(saml_admission.SAMLAdmissionError, match="escapes root"):
        saml_admission._confined_file(root_input, resolved, str(outside))
    with pytest.raises(saml_admission.SAMLAdmissionError, match="unavailable"):
        saml_admission._confined_file(root_input, resolved, "missing.xml")

    empty = root / "empty.xml"
    empty.write_bytes(b"")
    with pytest.raises(saml_admission.SAMLAdmissionError, match="lowercase SHA-256"):
        saml_admission._stable_read(empty, "invalid")
    with pytest.raises(saml_admission.SAMLAdmissionError, match="between 1 byte"):
        saml_admission._stable_read(empty, hashlib.sha256(b"").hexdigest())

    with pytest.raises(saml_admission.SAMLAdmissionError, match="well-formed"):
        saml_admission._parse_xml(b"<broken>")
    monkeypatch.setattr(saml_admission, "MAX_NODES", 0)
    with pytest.raises(saml_admission.SAMLAdmissionError, match="element limit"):
        saml_admission._parse_xml(b"<root/>")
    monkeypatch.setattr(saml_admission, "MAX_NODES", 10_000)
    monkeypatch.setattr(saml_admission, "MAX_DEPTH", 0)
    with pytest.raises(saml_admission.SAMLAdmissionError, match="nesting limit"):
        saml_admission._parse_xml(b"<root><child/></root>")

    duplicate_attributes = etree.fromstring(b'<root ID="one" id="two"/>')
    with pytest.raises(saml_admission.SAMLAdmissionError, match="multiple ID"):
        saml_admission._unique_ids(duplicate_attributes)
    monkeypatch.setattr(saml_admission, "MAX_IDS", 0)
    with pytest.raises(saml_admission.SAMLAdmissionError, match="ID limit"):
        saml_admission._unique_ids(etree.fromstring(b'<root ID="one"/>'))


def test_protocol_timestamp_and_claim_guard_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    assertion = _assertion()
    assert _admit(etree.tostring(assertion), assertion)["admitted"] is True

    response = etree.fromstring(_response(_assertion()))
    response.find(f".//{{{SAMLP}}}StatusCode").set("Value", "failure")
    with pytest.raises(saml_admission.SAMLAdmissionError, match="not Success"):
        _admit(etree.tostring(response))
    with pytest.raises(saml_admission.SAMLAdmissionError, match="root"):
        _admit(b"<root/>")

    unknown_algorithm = _assertion()
    unknown_algorithm.find(f".//{{{DS}}}SignatureMethod").set("Algorithm", "unknown")
    with pytest.raises(saml_admission.SAMLAdmissionError, match="SHA-2"):
        _admit(_response(unknown_algorithm), unknown_algorithm)
    with pytest.raises(saml_admission.SAMLAdmissionError, match="valid timestamp"):
        saml_admission._instant("not-a-time", "instant")
    with pytest.raises(saml_admission.SAMLAdmissionError, match="timezone"):
        saml_admission._instant("2026-10-09T12:00:00", "instant")

    conditions = _assertion().find(f"{{{SAML}}}Conditions")
    assert conditions is not None
    del conditions.attrib["NotBefore"]
    with pytest.raises(saml_admission.SAMLAdmissionError, match="define NotBefore"):
        saml_admission._validate_window(
            conditions,
            current=NOW,
            skew=timedelta(),
            label="Conditions",
            require_start=True,
        )
    conditions.set("NotBefore", (NOW + timedelta(minutes=1)).isoformat())
    del conditions.attrib["NotOnOrAfter"]
    with pytest.raises(saml_admission.SAMLAdmissionError, match="define NotOnOrAfter"):
        saml_admission._validate_window(
            conditions,
            current=NOW,
            skew=timedelta(),
            label="Conditions",
            require_start=True,
        )
    conditions.set("NotOnOrAfter", (NOW + timedelta(minutes=2)).isoformat())
    with pytest.raises(saml_admission.SAMLAdmissionError, match="not yet valid"):
        saml_admission._validate_window(
            conditions,
            current=NOW,
            skew=timedelta(),
            label="Conditions",
            require_start=True,
        )
    conditions.set("NotBefore", (NOW + timedelta(minutes=3)).isoformat())
    with pytest.raises(saml_admission.SAMLAdmissionError, match="window is invalid"):
        saml_admission._validate_window(
            conditions,
            current=NOW + timedelta(minutes=4),
            skew=timedelta(minutes=5),
            label="Conditions",
            require_start=True,
        )

    repeated_statement = _assertion()
    etree.SubElement(repeated_statement, f"{{{SAML}}}AttributeStatement")
    with pytest.raises(saml_admission.SAMLAdmissionError, match="at most one"):
        saml_admission._claims(repeated_statement, frozenset({"role"}))
    no_value = _assertion()
    value = no_value.find(f".//{{{SAML}}}AttributeValue")
    assert value is not None
    value.getparent().remove(value)
    with pytest.raises(saml_admission.SAMLAdmissionError, match="value count"):
        saml_admission._claims(no_value, frozenset({"role"}))
    monkeypatch.setattr(saml_admission, "MAX_CLAIMS", 0)
    with pytest.raises(saml_admission.SAMLAdmissionError, match="claim limit"):
        saml_admission._claims(_assertion(), frozenset({"role"}))


def test_verifier_allowlist_and_result_guard_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = _response(_assertion())
    with pytest.raises(TypeError, match="list of claim names"):
        _admit(raw, allowed_claims="role")
    with pytest.raises(saml_admission.SAMLAdmissionError, match="visible ASCII"):
        _admit(raw, allowed_claims=["bad\nname"])
    with pytest.raises(saml_admission.SAMLAdmissionError, match="unique"):
        _admit(raw, allowed_claims=["role", "role"])
    with pytest.raises(TypeError, match="missing required"):
        saml_admission.action_arguments({})

    class Raises:
        def verify(self, *_args: object, **_kwargs: object) -> object:
            raise ValueError("sensitive verifier detail")

    with pytest.raises(saml_admission.SAMLAdmissionError, match="verification failed") as failure:
        saml_admission.admit_saml_assertion(
            **_arguments(raw), verifier_factory=Raises, now=lambda: NOW
        )
    assert "sensitive" not in str(failure.value)

    for verified, message in (
        (None, "verified XML"),
        (etree.fromstring(b"<root/>"), "one SAML Assertion"),
        (_assertion(), "identity"),
    ):
        if isinstance(verified, etree._Element) and etree.QName(verified).localname == "Assertion":
            verified.set("ID", "other-id")
        with pytest.raises(saml_admission.SAMLAdmissionError, match=message):
            saml_admission.admit_saml_assertion(
                **_arguments(raw),
                verifier_factory=_Verified(verified),
                now=lambda: NOW,
            )

    with pytest.raises(saml_admission.SAMLAdmissionError, match="timezone-aware"):
        saml_admission.admit_saml_assertion(
            **_arguments(raw),
            verifier_factory=_Verified(_assertion()),
            now=lambda: NOW.replace(tzinfo=None),
        )
    wrong_method = _assertion()
    wrong_method.find(f".//{{{SAML}}}SubjectConfirmation").set("Method", "holder-of-key")
    with pytest.raises(saml_admission.SAMLAdmissionError, match="bearer"):
        _admit(raw, wrong_method)

    without_claims = _assertion()
    statement = without_claims.find(f"{{{SAML}}}AttributeStatement")
    assert statement is not None
    without_claims.remove(statement)
    monkeypatch.setattr(saml_admission, "MAX_RESULT_BYTES", 1)
    with pytest.raises(saml_admission.SAMLAdmissionError, match="result exceeds"):
        _admit(raw, without_claims, allowed_claims=[])
