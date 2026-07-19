# `general_ludd.radio.spectrum_scan` — Wideband Spectrum Scanner

Sweep RF spectrum using rtl_power — detect active signals, measure occupancy, identify bands.

## Quick start

```yaml
- name: Scan 2m ham band
  hosts: localhost
  vars:
    spectrum_scan_enabled: true
    spectrum_scan_start_freq_hz: 144000000
    spectrum_scan_end_freq_hz: 148000000
    spectrum_scan_bin_size_hz: 10000
  roles:
    - general_ludd.radio.spectrum_scan
```

## Variables

| Variable | Default | Description |
|---|---|---|
| `spectrum_scan_enabled` | `false` | Enable scan |
| `spectrum_scan_start_freq_hz` | `24000000` | Start frequency in Hz |
| `spectrum_scan_end_freq_hz` | `1700000000` | End frequency in Hz |
| `spectrum_scan_bin_size_hz` | `10000` | Frequency bin size in Hz |
| `spectrum_scan_integration_time_ms` | `100` | Integration time per bin |
| `spectrum_scan_gain` | `auto` | Gain setting |
