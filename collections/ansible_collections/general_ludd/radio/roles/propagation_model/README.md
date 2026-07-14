# `general_ludd.radio.propagation_model` — RF Path Loss Modeling

Compute path loss using ITM Longley-Rice, Hata-Okumura, free-space, and two-ray models.

## Quick start

```yaml
- name: Compute free-space path loss at 1 km
  hosts: localhost
  vars:
    propagation_model_enabled: true
    propagation_model_type: free_space
    propagation_model_freq_hz: 144000000
    propagation_model_distance_m: 1000
  roles:
    - general_ludd.radio.propagation_model
```

## Variables

| Variable | Default | Description |
|---|---|---|
| `propagation_model_enabled` | `false` | Enable computation |
| `propagation_model_type` | `free_space` | free_space / hata / itm / two_ray |
| `propagation_model_freq_hz` | `144000000` | Frequency in Hz |
| `propagation_model_tx_height_m` | `10.0` | Transmitter height in meters |
| `propagation_model_rx_height_m` | `1.5` | Receiver height in meters |
| `propagation_model_distance_m` | `1000.0` | Distance in meters |
