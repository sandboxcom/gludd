# macos_diagnose

Gather macOS diagnostic data from unified log, spdisaster_report, launchctl
service state, nvram variables, and pmset power settings. Produces a structured
JSON artifact with per-source findings.

## Output bounds

Each host command is time-bounded. Its stdout is spooled to a temporary file
instead of being accumulated by `subprocess`, and at most 8 MiB per command is
decoded into the artifact. This keeps high-volume unified logs from exhausting
the controller or xdist worker while preserving a useful diagnostic prefix.

This limit reflects long-lived operator reports rather than a synthetic test
constraint. Users report that an unfiltered `log show` is both
[costly and slow](https://stackoverflow.com/questions/75240751/launchd-based-logging-where-does-log-show-get-its-data-from)
and that its output can be
[huge even with a short time window](https://stackoverflow.com/questions/72965547/where-we-can-find-log-file-of-kexts-in-mac-os).
Apple's OSLog forum likewise documents legitimate
[high-volume logging and quarantine](https://developer.apple.com/forums/tags/oslog),
so the gatherer must remain bounded even when the host itself is unusually
chatty.
