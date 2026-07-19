# `general_ludd.radio.decode_digital` — Digital Mode Decoding

Decode DMR, P25, NXDN, D-STAR, APRS, FT8, and WSJT-X signals.

## Quick start

```yaml
- name: Decode digital voice
  hosts: localhost
  vars:
    decode_digital_enabled: true
    decode_digital_mode: auto
    decode_digital_input_file: /tmp/gludd-sdr-capture/iq_samples.wav
  roles:
    - general_ludd.radio.decode_digital
```

## Variables

| Variable | Default | Description |
|---|---|---|
| `decode_digital_enabled` | `false` | Enable decoding |
| `decode_digital_mode` | `auto` | auto / dmr / p25 / nxdn / dstar / aprs / ft8 / wsjt |
| `decode_digital_input_file` | `...iq_samples.bin` | Input file path |
| `decode_digital_sample_rate` | `2048000` | Sample rate |
