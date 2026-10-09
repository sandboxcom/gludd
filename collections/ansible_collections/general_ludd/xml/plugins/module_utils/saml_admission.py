"""Fail-closed, bounded admission of one cryptographically verified SAML assertion."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from lxml import etree
from signxml.algorithms import DigestAlgorithm, SignatureMethod
from signxml.exceptions import SignXMLException
from signxml.verifier import SignatureConfiguration, XMLVerifier

SIGNXML_VERSION = "5.1.0"
MAX_XML_BYTES = 1024 * 1024
MAX_CERTIFICATE_BYTES = 32 * 1024
MAX_NODES = 10_000
MAX_DEPTH = 64
MAX_IDS = 256
MAX_CLAIMS = 32
MAX_VALUES_PER_CLAIM = 16
MAX_VALUE_BYTES = 1024
MAX_RESULT_BYTES = 64 * 1024
MAX_CLOCK_SKEW_SECONDS = 120

SAML_NS = "urn:oasis:names:tc:SAML:2.0:assertion"
SAMLP_NS = "urn:oasis:names:tc:SAML:2.0:protocol"
DS_NS = "http://www.w3.org/2000/09/xmldsig#"
XSLT_NS = "http://www.w3.org/1999/XSL/Transform"
SUCCESS_STATUS = "urn:oasis:names:tc:SAML:2.0:status:Success"
BEARER_METHOD = "urn:oasis:names:tc:SAML:2.0:cm:bearer"
NS = {"saml": SAML_NS, "samlp": SAMLP_NS, "ds": DS_NS}

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SAFE_NAME = re.compile(r"^[\x21-\x7e]{1,256}$")
_ALLOWED_SIGNATURE_METHODS = frozenset(
    {
        SignatureMethod.RSA_SHA256,
        SignatureMethod.RSA_SHA384,
        SignatureMethod.RSA_SHA512,
        SignatureMethod.ECDSA_SHA256,
        SignatureMethod.ECDSA_SHA384,
        SignatureMethod.ECDSA_SHA512,
        SignatureMethod.SHA256_RSA_MGF1,
        SignatureMethod.SHA384_RSA_MGF1,
        SignatureMethod.SHA512_RSA_MGF1,
    }
)
_ALLOWED_DIGEST_ALGORITHMS = frozenset(
    {DigestAlgorithm.SHA256, DigestAlgorithm.SHA384, DigestAlgorithm.SHA512}
)
class SAMLAdmissionError(ValueError):
    """Stable rejection that is safe to expose through the Ansible boundary."""

    def as_result(self) -> dict[str, object]:
        """Return a bounded result without XML, certificates, claims, or paths."""
        return {
            "admitted": False,
            "changed": False,
            "failed": True,
            "msg": " ".join(str(self).split())[:512],
        }


_ALLOWED_ARGS = frozenset(
    {
        "allowed_claims", "clock_skew_seconds", "expected_audience",
        "expected_destination", "expected_in_response_to", "expected_issuer",
        "root", "saml_path", "saml_sha256", "saml_xml", "trusted_certificate",
    }
)
VerifierFactory = Callable[[], object]
Clock = Callable[[], datetime]


def _bounded_text(value: object, label: str, *, maximum: int = 2048) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise SAMLAdmissionError(f"{label} must be a non-empty string")
    if len(value.encode("utf-8")) > maximum:
        raise SAMLAdmissionError(f"{label} exceeds its byte limit")
    return value


def _root(value: object) -> tuple[Path, Path]:
    supplied = Path(_bounded_text(value, "root", maximum=4096))
    if not supplied.is_absolute():
        raise SAMLAdmissionError("root must be an absolute local path")
    if supplied.is_symlink():
        raise SAMLAdmissionError("root must not be a symlink")
    try:
        supplied_metadata = os.stat(supplied, follow_symlinks=False)
    except OSError as exc:
        raise SAMLAdmissionError("root is unavailable") from exc
    if not stat.S_ISDIR(supplied_metadata.st_mode):
        raise SAMLAdmissionError("root must be a directory")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(supplied, flags)
    except OSError as exc:
        raise SAMLAdmissionError("root is unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise SAMLAdmissionError("root must be a directory")
        resolved = supplied.resolve(strict=True)
        if supplied.is_symlink() or not resolved.is_dir():
            raise SAMLAdmissionError("root must not be a symlink")
    except OSError as exc:
        raise SAMLAdmissionError("root is unavailable") from exc
    finally:
        os.close(descriptor)
    return supplied, resolved


def _confined_file(root_input: Path, root: Path, value: object) -> Path:
    text = _bounded_text(value, "saml_path", maximum=4096)
    supplied = Path(text)
    if ".." in supplied.parts:
        raise SAMLAdmissionError("saml_path escapes root")
    candidate = supplied if supplied.is_absolute() else root_input / supplied
    try:
        relative = candidate.relative_to(root_input)
    except ValueError as exc:
        raise SAMLAdmissionError("saml_path escapes root") from exc
    current = root_input
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise SAMLAdmissionError("saml_path must not contain a symlink")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise SAMLAdmissionError("saml_path escapes root or is unavailable") from exc
    return resolved


def _stable_read(path: Path, expected_digest: object) -> tuple[bytes, str]:
    if not isinstance(expected_digest, str) or _DIGEST.fullmatch(expected_digest) is None:
        raise SAMLAdmissionError("saml_sha256 must be a lowercase SHA-256 digest")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SAMLAdmissionError("SAML file cannot be opened safely") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise SAMLAdmissionError("SAML input must be one regular unlinked file")
        if not (0 < before.st_size <= MAX_XML_BYTES):
            raise SAMLAdmissionError("SAML input must be between 1 byte and 1 MiB")
        chunks: list[bytes] = []
        remaining = MAX_XML_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after or len(content) != before.st_size:
        raise SAMLAdmissionError("SAML input changed while it was read")
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected_digest:
        raise SAMLAdmissionError("SAML input digest does not match saml_sha256")
    return content, actual


def _source(
    *,
    root: object,
    saml_path: object,
    saml_sha256: object,
    saml_xml: object,
) -> tuple[bytes, str]:
    has_inline = isinstance(saml_xml, str) and bool(saml_xml)
    has_path = isinstance(saml_path, str) and bool(saml_path)
    if has_inline == has_path:
        raise SAMLAdmissionError("exactly one of saml_xml or saml_path is required")
    if has_inline:
        if root not in (None, "") or saml_sha256 not in (None, ""):
            raise SAMLAdmissionError("inline SAML must not provide root or saml_sha256")
        content = cast(str, saml_xml).encode("utf-8")
        if not (0 < len(content) <= MAX_XML_BYTES):
            raise SAMLAdmissionError("SAML input must be between 1 byte and 1 MiB")
        return content, hashlib.sha256(content).hexdigest()
    root_input, resolved_root = _root(root)
    file_path = _confined_file(root_input, resolved_root, saml_path)
    return _stable_read(file_path, saml_sha256)


def _certificate(value: object) -> str:
    certificate = _bounded_text(
        value,
        "trusted_certificate",
        maximum=MAX_CERTIFICATE_BYTES,
    ).strip()
    if (
        certificate.count("-----BEGIN CERTIFICATE-----") != 1
        or certificate.count("-----END CERTIFICATE-----") != 1
    ):
        raise SAMLAdmissionError("trusted_certificate must contain exactly one PEM certificate")
    return certificate


def _parse_xml(content: bytes) -> etree._Element:
    upper = content.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise SAMLAdmissionError("DTD and entity declarations are forbidden")
    parser = etree.XMLParser(
        resolve_entities=False,
        load_dtd=False,
        no_network=True,
        recover=False,
        huge_tree=False,
        remove_comments=True,
    )
    try:
        root = etree.fromstring(content, parser=parser)
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise SAMLAdmissionError("SAML input is not well-formed XML") from exc
    nodes = list(root.iter())
    if len(nodes) > MAX_NODES:
        raise SAMLAdmissionError("SAML input exceeds the element limit")
    for node in nodes:
        depth = 0
        parent = node.getparent()
        while parent is not None:
            depth += 1
            if depth > MAX_DEPTH:
                raise SAMLAdmissionError("SAML input exceeds the nesting limit")
            parent = parent.getparent()
        qname = etree.QName(node)
        if qname.namespace == XSLT_NS:
            raise SAMLAdmissionError("XSLT content is forbidden")
        if qname.localname == "EncryptedAssertion":
            raise SAMLAdmissionError("encrypted assertions are not accepted")
    if root.getroottree().xpath("//processing-instruction()"):
        raise SAMLAdmissionError("XML processing instructions are forbidden")
    return root


def _unique_ids(root: etree._Element) -> None:
    seen: set[str] = set()
    for node in root.iter():
        identifiers = [value for key, value in node.attrib.items() if etree.QName(key).localname.lower() == "id"]
        if len(identifiers) > 1:
            raise SAMLAdmissionError("elements may not define multiple ID attributes")
        for identifier in identifiers:
            if not identifier or len(identifier.encode()) > 256 or identifier in seen:
                raise SAMLAdmissionError("SAML IDs must be non-empty, bounded, and unique")
            seen.add(identifier)
            if len(seen) > MAX_IDS:
                raise SAMLAdmissionError("SAML input exceeds the ID limit")


def _one(parent: etree._Element, path: str, label: str) -> etree._Element:
    matches = parent.xpath(path, namespaces=NS)
    if len(matches) != 1 or not isinstance(matches[0], etree._Element):
        raise SAMLAdmissionError(f"SAML input must contain exactly one {label}")
    return cast(etree._Element, matches[0])


def _raw_assertion(root: etree._Element, expected_issuer: str, destination: str, request_id: str) -> etree._Element:
    tag = etree.QName(root)
    if tag.namespace == SAMLP_NS and tag.localname == "Response":
        if root.get("Destination") != destination:
            raise SAMLAdmissionError("SAML Response destination does not match")
        if root.get("InResponseTo") != request_id:
            raise SAMLAdmissionError("SAML Response InResponseTo does not match")
        response_issuer = _bounded_text(_one(root, "./saml:Issuer", "Response Issuer").text, "Response Issuer")
        if response_issuer != expected_issuer:
            raise SAMLAdmissionError("SAML Response issuer does not match")
        status = _one(root, "./samlp:Status/samlp:StatusCode", "success StatusCode")
        if status.get("Value") != SUCCESS_STATUS:
            raise SAMLAdmissionError("SAML Response status is not Success")
        assertions = root.xpath("./saml:Assertion", namespaces=NS)
        if len(assertions) != 1:
            raise SAMLAdmissionError("SAML Response must contain exactly one Assertion")
        return cast(etree._Element, assertions[0])
    if tag.namespace == SAML_NS and tag.localname == "Assertion":
        return root
    raise SAMLAdmissionError("SAML root must be one Response or Assertion")


def _signature_contract(assertion: etree._Element) -> str:
    assertion_id = _bounded_text(assertion.get("ID"), "Assertion ID", maximum=256)
    signatures = assertion.xpath("./ds:Signature", namespaces=NS)
    all_signatures = assertion.xpath(".//ds:Signature", namespaces=NS)
    if len(signatures) != 1 or len(all_signatures) != 1:
        raise SAMLAdmissionError("Assertion must contain exactly one direct XML signature")
    signature = cast(etree._Element, signatures[0])
    references = signature.xpath("./ds:SignedInfo/ds:Reference", namespaces=NS)
    if len(references) != 1 or references[0].get("URI") != f"#{assertion_id}":
        raise SAMLAdmissionError("signature must reference only the Assertion ID")
    method = _one(signature, "./ds:SignedInfo/ds:SignatureMethod", "SignatureMethod").get("Algorithm")
    digest = _one(
        signature,
        "./ds:SignedInfo/ds:Reference/ds:DigestMethod",
        "DigestMethod",
    ).get("Algorithm")
    try:
        signature_algorithm = SignatureMethod(method)
        digest_algorithm = DigestAlgorithm(digest)
    except (TypeError, ValueError, SignXMLException) as exc:
        raise SAMLAdmissionError("signature must use an admitted SHA-2 algorithm") from exc
    if signature_algorithm not in _ALLOWED_SIGNATURE_METHODS or digest_algorithm not in _ALLOWED_DIGEST_ALGORITHMS:
        raise SAMLAdmissionError("signature must use an admitted SHA-2 algorithm")
    return assertion_id


def _instant(value: object, label: str) -> datetime:
    text = _bounded_text(value, label, maximum=64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SAMLAdmissionError(f"{label} is not a valid timestamp") from exc
    if parsed.tzinfo is None:
        raise SAMLAdmissionError(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def _validate_window(
    element: etree._Element,
    *,
    current: datetime,
    skew: timedelta,
    label: str,
    require_start: bool,
) -> tuple[str, str]:
    before_text = element.get("NotBefore")
    after_text = element.get("NotOnOrAfter")
    if require_start and before_text is None:
        raise SAMLAdmissionError(f"{label} must define NotBefore")
    if after_text is None:
        raise SAMLAdmissionError(f"{label} must define NotOnOrAfter")
    before = _instant(before_text, f"{label} NotBefore") if before_text is not None else None
    after = _instant(after_text, f"{label} NotOnOrAfter")
    if before is not None and current + skew < before:
        raise SAMLAdmissionError(f"{label} is not yet valid")
    if current - skew >= after:
        raise SAMLAdmissionError(f"{label} has expired")
    if before is not None and before >= after:
        raise SAMLAdmissionError(f"{label} validity window is invalid")
    return (before_text or "", after_text)


def _claims(assertion: etree._Element, allowed_claims: frozenset[str]) -> dict[str, list[str]]:
    statements = assertion.xpath("./saml:AttributeStatement", namespaces=NS)
    if len(statements) > 1:
        raise SAMLAdmissionError("Assertion must contain at most one AttributeStatement")
    attributes = [] if not statements else statements[0].xpath("./saml:Attribute", namespaces=NS)
    if len(attributes) > MAX_CLAIMS:
        raise SAMLAdmissionError("Assertion exceeds the claim limit")
    claims: dict[str, list[str]] = {}
    total = 0
    for raw_attribute in attributes:
        attribute = cast(etree._Element, raw_attribute)
        name = _bounded_text(attribute.get("Name"), "claim name", maximum=256)
        if name not in allowed_claims:
            raise SAMLAdmissionError("Assertion contains a claim outside allowed_claims")
        if name in claims:
            raise SAMLAdmissionError("Assertion contains a duplicate claim name")
        raw_values = attribute.xpath("./saml:AttributeValue", namespaces=NS)
        if not (1 <= len(raw_values) <= MAX_VALUES_PER_CLAIM):
            raise SAMLAdmissionError("claim value count is outside the allowed range")
        values: list[str] = []
        for raw_value in raw_values:
            value_node = cast(etree._Element, raw_value)
            if len(value_node):
                raise SAMLAdmissionError("claim values must be scalar text")
            value = _bounded_text(value_node.text, "claim value", maximum=MAX_VALUE_BYTES)
            total += len(name.encode()) + len(value.encode())
            if total > MAX_RESULT_BYTES:
                raise SAMLAdmissionError("claims exceed the result byte limit")
            values.append(value)
        claims[name] = values
    return dict(sorted(claims.items()))


def _admitted_claims(value: object) -> frozenset[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError("allowed_claims must be a list of claim names")
    if len(value) > MAX_CLAIMS:
        raise SAMLAdmissionError("allowed_claims exceeds the 32-claim limit")
    names: list[str] = []
    for item in value:
        name = _bounded_text(item, "allowed claim", maximum=256)
        if _SAFE_NAME.fullmatch(name) is None:
            raise SAMLAdmissionError("allowed claim names must contain visible ASCII")
        names.append(name)
    if len(names) != len(set(names)):
        raise SAMLAdmissionError("allowed_claims must be unique")
    return frozenset(names)


def action_arguments(args: Mapping[str, object]) -> dict[str, object]:
    """Validate the bounded public key surface and apply immutable defaults."""
    unsupported = sorted(set(args) - _ALLOWED_ARGS)
    if unsupported:
        raise TypeError(f"unsupported argument: {unsupported[0]}")
    required = (
        "trusted_certificate",
        "expected_issuer",
        "expected_audience",
        "expected_destination",
        "expected_in_response_to",
        "allowed_claims",
    )
    missing = [name for name in required if name not in args]
    if missing:
        raise TypeError(f"missing required argument: {missing[0]}")
    return {
        **args,
        "root": args.get("root", ""),
        "saml_path": args.get("saml_path", ""),
        "saml_sha256": args.get("saml_sha256", ""),
        "saml_xml": args.get("saml_xml", ""),
        "clock_skew_seconds": args.get("clock_skew_seconds", 30),
    }


def admit_saml_assertion(
    *,
    root: object = "",
    saml_path: object = "",
    saml_sha256: object = "",
    saml_xml: object = "",
    trusted_certificate: object,
    expected_issuer: object,
    expected_audience: object,
    expected_destination: object,
    expected_in_response_to: object,
    allowed_claims: object,
    clock_skew_seconds: object = 30,
    verifier_factory: VerifierFactory = XMLVerifier,
    now: Clock | None = None,
) -> dict[str, object]:
    """Admit one signature-verified SAML assertion and return bounded claims."""
    if isinstance(clock_skew_seconds, bool) or not isinstance(clock_skew_seconds, int):
        raise TypeError("clock_skew_seconds must be an integer")
    if not (0 <= clock_skew_seconds <= MAX_CLOCK_SKEW_SECONDS):
        raise SAMLAdmissionError("clock skew must be between 0 and 120 seconds")
    issuer = _bounded_text(expected_issuer, "expected_issuer")
    audience = _bounded_text(expected_audience, "expected_audience")
    destination = _bounded_text(expected_destination, "expected_destination")
    request_id = _bounded_text(expected_in_response_to, "expected_in_response_to", maximum=256)
    allowlist = _admitted_claims(allowed_claims)
    certificate = _certificate(trusted_certificate)
    content, digest = _source(
        root=root,
        saml_path=saml_path,
        saml_sha256=saml_sha256,
        saml_xml=saml_xml,
    )
    document = _parse_xml(content)
    _unique_ids(document)
    unverified_assertion = _raw_assertion(document, issuer, destination, request_id)
    assertion_id = _signature_contract(unverified_assertion)
    try:
        verifier = cast(XMLVerifier, verifier_factory())
        verified = verifier.verify(
            unverified_assertion,
            x509_cert=certificate,
            id_attribute="ID",
            expect_config=SignatureConfiguration(
                require_x509=True,
                location="./",
                expect_references=1,
                signature_methods=_ALLOWED_SIGNATURE_METHODS,
                digest_algorithms=_ALLOWED_DIGEST_ALGORITHMS,
                ignore_ambiguous_key_info=False,
            ),
        )
    except (SignXMLException, etree.Error, TypeError, ValueError) as exc:
        raise SAMLAdmissionError("SAML signature verification failed") from exc
    signed_xml = getattr(verified, "signed_xml", None)
    if not isinstance(signed_xml, etree._Element):
        raise SAMLAdmissionError("signature did not return a verified XML Assertion")
    signed_tag = etree.QName(signed_xml)
    if signed_tag.namespace != SAML_NS or signed_tag.localname != "Assertion":
        raise SAMLAdmissionError("signature did not cover one SAML Assertion")
    if signed_xml.get("ID") != assertion_id:
        raise SAMLAdmissionError("verified Assertion identity does not match the admitted Assertion")
    _unique_ids(signed_xml)

    current = (now or (lambda: datetime.now(tz=UTC)))()
    if current.tzinfo is None:
        raise SAMLAdmissionError("validation clock must be timezone-aware")
    current = current.astimezone(UTC)
    skew = timedelta(seconds=clock_skew_seconds)
    assertion_issuer = _bounded_text(
        _one(signed_xml, "./saml:Issuer", "Assertion Issuer").text,
        "Assertion Issuer",
    )
    if assertion_issuer != issuer:
        raise SAMLAdmissionError("verified Assertion issuer does not match")
    issue_instant = _instant(signed_xml.get("IssueInstant"), "Assertion IssueInstant")
    if issue_instant > current + skew:
        raise SAMLAdmissionError("Assertion IssueInstant is in the future")
    conditions = _one(signed_xml, "./saml:Conditions", "Conditions")
    not_before, not_on_or_after = _validate_window(
        conditions,
        current=current,
        skew=skew,
        label="Assertion Conditions",
        require_start=True,
    )
    audiences = conditions.xpath("./saml:AudienceRestriction/saml:Audience/text()", namespaces=NS)
    if audiences != [audience]:
        raise SAMLAdmissionError("verified Assertion audience does not match exactly")
    subject = _one(signed_xml, "./saml:Subject", "Subject")
    name_id = _bounded_text(_one(subject, "./saml:NameID", "NameID").text, "NameID", maximum=1024)
    confirmation = _one(subject, "./saml:SubjectConfirmation", "SubjectConfirmation")
    if confirmation.get("Method") != BEARER_METHOD:
        raise SAMLAdmissionError("SubjectConfirmation must use the bearer method")
    confirmation_data = _one(
        confirmation,
        "./saml:SubjectConfirmationData",
        "SubjectConfirmationData",
    )
    if confirmation_data.get("Recipient") != destination:
        raise SAMLAdmissionError("verified Assertion recipient does not match")
    if confirmation_data.get("InResponseTo") != request_id:
        raise SAMLAdmissionError("verified Assertion InResponseTo does not match")
    _validate_window(
        confirmation_data,
        current=current,
        skew=skew,
        label="SubjectConfirmationData",
        require_start=False,
    )
    claims = _claims(signed_xml, allowlist)
    result: dict[str, object] = {
        "admitted": True,
        "assertion_id": assertion_id,
        "audience": audience,
        "changed": False,
        "claims": claims,
        "destination": destination,
        "in_response_to": request_id,
        "issuer": issuer,
        "not_before": not_before,
        "not_on_or_after": not_on_or_after,
        "saml_sha256": digest,
        "subject": name_id,
    }
    if len(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()) > MAX_RESULT_BYTES:
        raise SAMLAdmissionError("admission result exceeds the 64 KiB limit")
    return result


__all__ = [
    "SIGNXML_VERSION",
    "SAMLAdmissionError",
    "action_arguments",
    "admit_saml_assertion",
]
