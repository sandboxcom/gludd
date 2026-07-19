# `general_ludd.radio.link_budget` — RF Link Budget Calculator

Compute complete RF link budget: EIRP, path loss, RX signal strength, and fade margin.

## Quick start

```yaml
- name: Calculate VHF link budget
  hosts: localhost
  vars:
    link_budget_enabled: true
    link_budget_freq_hz: 146000000
    link_budget_distance_m: 10000
    link_budget_tx_power_dbm: 37.0
  roles:
    - general_ludd.radio.link_budget
```

## Variables

| Variable | Default | Description |
|---|---|---|
| `link_budget_enabled` | `false` | Enable calculation |
| `link_budget_tx_power_dbm` | `30.0` | Transmitter power in dBm |
| `link_budget_tx_antenna_gain_dbi` | `2.15` | TX antenna gain in dBi |
| `link_budget_tx_line_loss_db` | `1.0` | TX feedline loss in dB |
| `link_budget_rx_antenna_gain_dbi` | `2.15` | RX antenna gain in dBi |
| `link_budget_rx_line_loss_db` | `1.0` | RX feedline loss in dB |
| `link_budget_path_loss_db` | `null` | Path loss in dB (or computed from model) |
| `link_budget_freq_hz` | `144000000` | Frequency in Hz |
| `link_budget_distance_m` | `10000.0` | Distance in meters |
| `link_budget_rx_sensitivity_dbm` | `-120.0` | Receiver sensitivity in dBm |
| `link_budget_required_snr_db` | `10.0` | Required SNR margin in dB |
| `link_budget_model` | `free_space` | Path loss model (if path_loss_db is null) |
