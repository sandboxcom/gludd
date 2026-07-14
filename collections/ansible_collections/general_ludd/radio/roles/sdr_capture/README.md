# `general_ludd.radio.sdr_capture` — SDR IQ Sample Capture

Capture raw IQ samples from SDR hardware.

## Quick start

```yaml
- name: Capture NOAA weather radio IQ
  hosts: localhost
  vars:
    sdr_capture_enabled: true
    sdr_capture_freq_hz: 162400000
    sdr_capture_sample_rate: 2048000
    sdr_capture_duration_sec: 5.0
  roles:
    - general_ludd.radio.sdr_capture
```

## Variables

| Variable | Default | Description |
|---|---|---|
| `sdr_capture_enabled` | `false` | Enable capture (safety gate) |
| `sdr_capture_device_index` | `0` | SDR device index |
| `sdr_capture_freq_hz` | `100000000` | Center frequency in Hz |
| `sdr_capture_sample_rate` | `2048000` | Sample rate in samples/sec |
| `sdr_capture_gain` | `auto` | Gain setting |
| `sdr_capture_duration_sec` | `1.0` | Capture duration in seconds |
| `sdr_capture_output_dir` | `/tmp/gludd-sdr-capture` | Output directory |
| `sdr_capture_format` | `int16` | IQ sample format |
| `sdr_capture_tool` | `rtl_sdr` | CLI tool for capture |
