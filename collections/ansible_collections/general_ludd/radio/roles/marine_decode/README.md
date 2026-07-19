# `general_ludd.radio.marine_decode` — Marine Communication Decoding

Decode AIS, NAVTEX, DSC, and EPIRB/SART marine signals.

## Quick start

```yaml
- name: Decode AIS traffic
  hosts: localhost
  vars:
    marine_decode_enabled: true
    marine_decode_mode: ais
    marine_decode_input_file: /tmp/gludd-sdr-capture/iq_samples.bin
  roles:
    - general_ludd.radio.marine_decode
```

## Variables

| Variable | Default | Description |
|---|---|---|
| `marine_decode_enabled` | `false` | Enable decoding |
| `marine_decode_mode` | `auto` | auto / ais / navtex / dsc |
| `marine_decode_input_file` | `...iq_samples.bin` | Input file path |
| `marine_decode_sample_rate` | `2048000` | Sample rate |
