#!/usr/bin/env python3
"""Compatibility CLI for the packaged signal-identification runtime."""

from ansible_collections.general_ludd.radio.plugins.module_utils.signal_identify_runtime import (
    main,
    signal_identify,
)

__all__ = ["main", "signal_identify"]


if __name__ == "__main__":
    main()
