# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424 AS uv

FROM docker.io/library/python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e

COPY --from=uv /uv /uvx /bin/

RUN python --version \
    && uv --version \
    && python -c 'import platform; assert platform.python_version() == "3.12.14"' \
    && case "$(uv --version)" in "uv 0.12.19"*) ;; *) exit 1 ;; esac
