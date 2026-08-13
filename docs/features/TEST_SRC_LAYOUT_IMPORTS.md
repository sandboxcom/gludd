# Test Imports for the Src Layout

## Problem

Seven unit files imported `src.general_ludd` directly. In a src-layout project,
`src` is a filesystem container, not the installed package name. Importing
through it can create a second module identity beside `general_ludd`, let a
checkout pass when a built wheel would fail, and make monkeypatch or singleton
state attach to the wrong module object.

## Contract

Tests import `general_ludd` exactly as users and the installed console
entrypoint do. The existing repository guard rejects any future
`from src.general_ludd` spelling. No source path is manually inserted and no
test-only namespace package is created.

## Practitioner evidence

Setuptools discussion
[#3872](https://github.com/pypa/setuptools/discussions/3872) is a long-lived
src-layout user report that observed duplicate installed modules under both the
real package and a `src`-prefixed name. The maintainer explains that `src`
selects where packages are discovered; it is not itself part of the package
name. The same boundary applies to Gludd's tests.

## ZDD, security, and resources

This is import-only test conformance. It causes no application downtime, starts
no process or service, and changes no runtime source. Exercising the installed
package identity improves supply-chain fidelity because local tests no longer
depend on an import path absent from the wheel.

## Verification

The seven affected unit files run under strict warnings, their adjacent domain
suites retain their behavior, Ruff passes, and the repository-wide namespace
guard finds zero offenders.
