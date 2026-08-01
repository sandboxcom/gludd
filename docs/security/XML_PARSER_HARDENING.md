# XML parser hardening

## Outcome

All Gludd XML entry points now cross one fail-closed parser boundary before the
document is used. The boundary uses the maintained `defusedxml` parser, forbids
DTD declarations, internal entities, and external references, and enforces
configurable byte, depth, and node limits. Lxml-only HTML, XPath, SAML, and XSLT
paths receive the same preflight and tree limits; their parsers disable network,
DTD loading, entity resolution, and `huge_tree`. XSLT also denies file and
network reads/writes through `XSLTAccessControl`.

The default policy is 4 MiB, 64 levels, and 100,000 elements. Callers that need
a smaller envelope pass `XmlSecurityLimits`; widening should be an explicit
reviewed configuration decision. A denial raises `XmlSecurityError` and emits a
redacted `XmlSecurityEvent` containing only reason, source label, byte count,
and limit. Services can inject their durable audit sink; without one, the same
bounded event is emitted to the service security log. XML bytes, entity values,
and referenced paths are never copied into the event.

## Migrated consumers and ZDD

- `xml_utils` parsing, SOAP, SAML, DocBook, XSD inference, XPath, HTML, and XSLT.
- code-intelligence and self-improvement `coverage.xml` readers.
- the preflight coverage reader.

The migration changes neither storage nor wire schemas. Safe documents retain
stdlib `Element`/`ElementTree` behavior, so mixed old/new Gunicorn workers can
run during a rolling deployment. Invalid or hostile XML fails before allocation
of downstream work; rollback is the prior application build and requires no
data migration.

## Verification and scanner disposition

The regression corpus covers DTD, internal/external entities, oversized files,
deep/wide trees, audit-sink failure, tolerant HTML, SOAP/SAML/DocBook consumers,
coverage readers, and XSLT local-file access. The focused suite also exercises
safe compatibility paths.

After migration, Bandit reports zero B314, B318, and B408 findings, eliminating
all XML medium findings. Two B405 low findings remain at the shared boundary and
`xml_utils`: these imports are used for compatible `Element` construction,
typing, and serialization only. Every parse call is mechanically routed through
`defusedxml`; there is no suppression or blanket Bandit skip. The low import
signal is retained visibly until Bandit can distinguish construction from parse.

## Long-lived operator evidence

No parallel web-research job was started because security research is serialized
for this project. This implementation reuses the operator evidence already
recorded in `FEATURE_SECURITY_SANDBOX_HARDENING.md`: Bubblewrap issue
[#324](https://github.com/containers/bubblewrap/issues/324) has documented
environment-dependent isolation failures since 2019, and nsjail issue
[#236](https://github.com/google/nsjail/issues/236) records a 2024 host-policy
upgrade breaking isolation setup. Although those reports concern sandbox
runtimes rather than XML, the durable lesson applies here: dependency presence
is not proof of enforcement. Gludd therefore tests malicious payload denial at
the actual shared entry points and keeps byte/complexity controls independent of
the parser's own defaults.
