# `general_ludd.radio.signal_identify` — Signal Classification

Classify modulation type, baud rate, and protocol from IQ sample data.

## Quick start

```yaml
- name: Identify signals in IQ data
  hosts: localhost
  vars:
    signal_identify_enabled: true
    signal_identify_input_file: /tmp/gludd-sdr-capture/iq_samples.bin
    signal_identify_sample_rate: 2048000
    signal_identify_method: fft
  roles:
    - general_ludd.radio.signal_identify
```

## Variables

| Variable | Default | Description |
|---|---|---|
| `signal_identify_enabled` | `false` | Enable identification |
| `signal_identify_input_file` | `/tmp/gludd-sdr-capture/iq_samples.bin` | IQ sample input |
| `signal_identify_sample_rate` | `2048000` | Sample rate in samples/sec |
| `signal_identify_method` | `fft` | fft / cyclostationary / auto |
| `signal_identify_threshold_db` | `10.0` | Peak detection threshold in dB |
