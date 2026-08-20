# networking

`general_ludd.networking.networking` — comprehensive networking knowledge and operations
role for the `general_ludd.agent` collection. Designed as a sub-role callable from
diagnostic and infrastructure playbooks via `include_role`.

## Description

Covers 7 modes spanning packet analysis, traffic inspection, network discovery,
dissector development, and diagnostic tooling. Integrates with `gludd_scapy` for
packet-level operations and Wireshark Lua dissector generation.

1. **PCAP Read** — read and summarize packet captures; extract protocol hierarchy,
   packet counts, and structured packet summaries from pcap/pcapng files
2. **Packet Craft** — build packet specifications from protocol stacks and field
   definitions; optionally send crafted packets on a specified interface
3. **Network Scan** — run host discovery, port scanning, and service enumeration
   via nmap (syn, tcp, udp, version, os scan types)
4. **Traffic Analysis** — produce TrafficReport with protocol distribution,
   top talkers, top ports, flow analysis, and capture duration
5. **Dissector Creation** — generate Wireshark Lua protocol dissectors from a
   template with custom field registration, parsing logic, and port binding
6. **Tool Recommendations** — match diagnostic scenarios (packet loss, TLS errors,
   DNS failures, ARP spoofing, etc.) to the top 2-3 tools with command examples
7. **Packet Dissection** — dissect raw bytes into protocol layers via
   `ScapyAdapter.dissect_packet`

## Variables

| Variable | Type | Default | Description |
|---|---|---|---|
| `networking__artifact_dir` | str | `/tmp/gludd-networking` | Artifact output directory |
| `networking__daemon_url` | str | `http://localhost:8000` | Daemon URL for model calls |
| `networking__psk` | str | `""` | Pre-shared key (no_log enforced) |
| `networking__mode` | str | `lookup` | Operation mode (see Modes table) |
| `networking__pcap_path` | str | `""` | Path to pcap/pcapng file for read/analyze/dissect |
| `networking__protocol_stack` | list | `[]` | Protocol layers to craft (e.g. `["Ether", "IP", "TCP"]`) |
| `networking__packet_fields` | dict | `{}` | Per-layer field overrides for packet craft |
| `networking__interface` | str | `eth0` | Network interface for send/sniff/scan |
| `networking__count` | int | `1` | Packet count for send/sniff operations |
| `networking__timeout` | int | `30` | Timeout seconds for sniff/scan operations |
| `networking__output_format` | str | `json` | Output format for parsed results |
| `networking__filter_str` | str | `""` | BPF filter string for pcap/sniff operations |
| `networking__scan_target` | str | `""` | Target host, CIDR, or IP range for nmap scans |
| `networking__scan_ports` | str | `""` | Port range for scan (e.g. "1-1000", "80,443") |
| `networking__scan_type` | str | `syn` | nmap scan type: syn, tcp, udp, version, os |
| `networking__dissector_name` | str | `""` | Protocol name for generated dissector |
| `networking__dissector_language` | str | `lua` | Dissector language: lua (supported), c (future) |
| `networking__dissector_fields` | list | `[]` | Field definitions (dicts: name, type, description, offset, size) |
| `networking__dissector_port` | int | `0` | Port number to bind dissector to |
| `networking__diagnostic_scenario` | str | `""` | Diagnostic scenario for tool recommendation |
| `networking__tool_context` | str | `""` | Additional context for tool commands (hostname, URL, etc.) |
| `networking__send` | bool | `false` | Send crafted packets after building spec |

## Knowledge Data Files

| File | Description |
|---|---|
| `vars/tools.yml` | Diagnostic tool registry: per-scenario tool names, descriptions, commands |
| `vars/protocols.yml` | Protocol reference: IANA protocol numbers, common port assignments |
| `vars/networks.yml` | Network reference: well-known CIDRs, multicast ranges, private ranges |
| `vars/rfcs.yml` | RFC index for networking protocols referenced in the role |

## Modes

| Mode | Description | Required vars |
|---|---|---|
| `pcap_read` | Read and summarize a pcap file | `pcap_path` |
| `packet_craft` | Craft and optionally send packets | `protocol_stack`, `packet_fields` |
| `network_scan` | Run nmap port/host discovery scan | `scan_target` |
| `traffic_analyze` | Analyze pcap traffic (protocols, talkers, flows) | `pcap_path` |
| `dissector_create` | Generate Wireshark Lua dissector from template | `dissector_name`, `dissector_language` |
| `tool_recommend` | Recommend diagnostic tools for scenario | `diagnostic_scenario` |
| `packet_dissect` | Dissect raw bytes into protocol layers | `pcap_path` or raw bytes spec |

## Artifacts

| File | Mode |
|---|---|
| `<artifact_dir>/pcap_read.json` | pcap_read |
| `<artifact_dir>/packet_craft.json` | packet_craft |
| `<artifact_dir>/network_scan.json` | network_scan |
| `<artifact_dir>/traffic_analyze.json` | traffic_analyze |
| `<artifact_dir>/dissector_created.json` | dissector_create |
| `<artifact_dir>/tool_recommendations.json` | tool_recommend |
| `<artifact_dir>/<dissector_name>.lua` | dissector_create (generated Lua file) |

## Security

- `networking__psk` is `no_log: true` on every task accessing the daemon
- Artifact files written with mode `0644` to `networking__artifact_dir`
- Packet capture and sniff operations run only when `not ansible_check_mode`
- Raw packet data payloads are truncated to 64 bytes in artifact output
- nmap scan operations respect `ansible_check_mode` — dry-run safe by default

## Integration

This role delegates to two Python adapters:

- **`general_ludd.networking.scapy_adapter`** (`src/general_ludd/networking/scapy_adapter.py`):
  `read_pcap()`, `craft_packet()`, `send_packet()`, `analyze_pcap()`, `dissect_packet()`,
  `sniff_packets()`. Provides structured Python return types: `list[PacketSummary]`,
  `TrafficReport`, `dict` for spec/send results.
- **`general_ludd.networking.gludd_scapy`** module: Ansible-native wrapper that translates
  YAML task inputs to ScapyAdapter calls. Actions: `read_pcap`, `write_pcap`,
  `craft_packet`, `send_packet`, `sniff_packets`, `analyze_pcap`, `dissect_packet`.

**Dissector template:** `files/dissector_template.lua` (212 lines) provides a full
Wireshark Lua dissector scaffold with field registration, subtrees, expert info
handlers, heuristic detection, and TCP reassembly support. An example GPS protocol
dissector is included at `files/dissector_example.lua` (228 lines).

## Wireless and Infrastructure

The knowledge vars files expand the role's diagnostic reach:

- `vars/tools.yml` — maps diagnostic scenarios to tool-command chains for
  packet analysis, bandwidth measurement, DNS troubleshooting, TLS debugging,
  ARP monitoring, and routing diagnostics
- `vars/protocols.yml` — IANA protocol number assignments, common TCP/UDP port
  mappings, and protocol stack reference for packet crafting
- `vars/networks.yml` — well-known CIDR blocks (RFC 1918, RFC 6598, RFC 3927,
  multicast, link-local), private address ranges, and BGP community references
- `vars/rfcs.yml` — networking RFC index cross-referenced by protocol and feature
  area covered by the role

## Usage

```yaml
- name: Read and summarize a packet capture
  ansible.builtin.include_role:
    name: general_ludd.networking.networking
  vars:
    networking__mode: pcap_read
    networking__pcap_path: /tmp/capture.pcap

- name: Scan a target for open ports with service versions
  ansible.builtin.include_role:
    name: general_ludd.networking.networking
  vars:
    networking__mode: network_scan
    networking__scan_target: 192.168.1.0/24
    networking__scan_ports: "22,80,443,3306,5432"
    networking__scan_type: version

- name: Recommend tools for TLS handshake failure diagnosis
  ansible.builtin.include_role:
    name: general_ludd.networking.networking
  vars:
    networking__mode: tool_recommend
    networking__diagnostic_scenario: tls_error
    networking__tool_context: "api.example.com:443"
```
