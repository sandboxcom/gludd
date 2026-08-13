# Self-Update Public-Key File Lifecycle

## Status

Implemented for beta.4 self-update signature verification.

## Problem

`load_public_key()` read both an explicitly configured key path and the
`GLUDD_SELF_UPDATE_PUBLIC_KEY_FILE` path with `open(path).read()`. The file
object had no deterministic owner or close point. Warning-strict tests therefore
raised `ResourceWarning`, and repeated self-update checks could retain descriptors
until garbage collection.

Public keys are part of the fail-closed update trust boundary. Their contents must be
read completely and normalized without weakening resolution priority or signature
verification.

## Contract

1. An existing explicit `key_path` remains the highest-priority source.
2. An inline `GLUDD_SELF_UPDATE_PUBLIC_KEY` remains second and opens no file.
3. `GLUDD_SELF_UPDATE_PUBLIC_KEY_FILE` remains the final configured source.
4. Every file source is read as UTF-8 through one context-managed helper.
5. The descriptor is closed before the key string is returned, including when
   reading or decoding raises.
6. Leading and trailing whitespace is stripped exactly as before.
7. Missing paths and missing configuration still return the empty string so
   signature verification fails closed.

## Zero-Downtime Development Evidence

The existing warning-strict replay failed three public-key loading cases with
unclosed-file `ResourceWarning` messages. A deterministic failing-first test then
proved the explicit path did not enter a context manager and omitted its encoding.

After the repair, the complete signing-verification suite plus all public-key
resolution cases pass 17/17 under `-W error` with 100 percent branch coverage
for `signing.py`. The change is local to file reading;
it changes no key format, signature algorithm, environment variable, API response,
database schema, or service state, so old and new workers can overlap during a
rolling deployment.

## Security and Resource Boundaries

The shared helper reads only a path that passed the existing `isfile` check. It
does not broaden path selection, log key material, cache secrets, or change the
fail-closed empty-key result. Deterministic close bounds file-descriptor use to one
descriptor per active read and prevents garbage-collector timing from becoming part
of the security path.

## Practitioner Evidence

[Python Help discussion "ResourceWarning: unclosed file"](https://discuss.python.org/t/python-message-resourcewarning-unclosed-file/105824)
shows the same long-lived `open(...).read()` pattern across Python versions and
recommends a `with` block or `Path.read_text()` so close happens deterministically.

[CPython's ResourceWarning documentation](https://github.com/python/cpython/blob/main/Doc/whatsnew/3.2.rst)
explains that destroying a file object without explicitly closing it emits the
warning and that delayed cleanup can cause platform-specific resource problems.

The project therefore treats this warning as a lifecycle defect rather than
suppressing it.
