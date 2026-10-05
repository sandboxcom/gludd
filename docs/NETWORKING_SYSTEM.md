# Networking System

Comprehensive networking knowledge and operations role for the gludd agent platform.
Covers packet analysis, traffic inspection, network discovery, dissector development,
and diagnostic tooling. Integrates with the Scapy adapter for packet-level operations
and Wireshark Lua dissector generation.

## Architecture Overview

```text
┌──────────────────────────────────────────────────────────────────┐
│                        Agent / Playbook                          │
│         (FQCN: general_ludd.networking.networking)               │
└───────────────┬──────────────────────────────────┬───────────────┘
                │                                  │
    ┌───────────▼───────────┐          ┌───────────▼───────────┐
    │   networking role     │          │   ScapyAdapter        │
    │  (7 modes, YAML vars) │          │  (src/networking/)    │
    └───────────┬───────────┘          └───────────┬───────────┘
                │                                  │
    ┌───────────▼──────────────────────────────────▼───────────┐
    │                  Knowledge Data Files                     │
    │  tools.yml  protocols.yml  networks.yml  rfcs.yml        │
    └───────────┬──────────────────────────────────────────────┘
                │
    ┌───────────▼───────────┐
    │   Dissector Templates │
    │  dissector_template   │
    │  .lua (212 lines)     │
    │  dissector_example    │
    │  .lua (228 lines)     │
    └───────────────────────┘
```

### Data flow

1. **Agent** invokes the role via FQCN with mode-specific variables
2. **Role** dispatches to `ScapyAdapter` methods for packet operations or
   `gludd_model_call` for LLM-assisted analysis
3. **ScapyAdapter** reads/writes pcap files, crafts/sends packets, dissects
   raw bytes into protocol layers
4. **Dissector templates** generate Wireshark Lua protocol dissectors from
   field definitions

## Role Reference

### `general_ludd.networking.networking`

**FQCN:** `general_ludd.networking.networking`

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

### Modes

| Mode | Description | Required vars |
|---|---|---|
| `pcap_read` | Read and summarize a pcap file | `pcap_path` |
| `packet_craft` | Craft and optionally send packets | `protocol_stack`, `packet_fields` |
| `network_scan` | Run nmap port/host discovery scan | `scan_target` |
| `traffic_analyze` | Analyze pcap traffic (protocols, talkers, flows) | `pcap_path` |
| `dissector_create` | Generate Wireshark Lua dissector from template | `dissector_name`, `dissector_language` |
| `tool_recommend` | Recommend diagnostic tools for scenario | `diagnostic_scenario` |
| `packet_dissect` | Dissect raw bytes into protocol layers | `pcap_path` or raw bytes spec |

### Knowledge Data Files

| File | Description |
|---|---|
| `vars/tools.yml` | Diagnostic tool registry: per-scenario tool names, descriptions, commands |
| `vars/protocols.yml` | Protocol reference: IANA protocol numbers, common port assignments |
| `vars/networks.yml` | Network reference: well-known CIDRs, multicast ranges, private ranges |
| `vars/rfcs.yml` | RFC index for networking protocols referenced in the role |

### Artifacts

| File | Mode |
|---|---|
| `<artifact_dir>/pcap_read.json` | pcap_read |
| `<artifact_dir>/packet_craft.json` | packet_craft |
| `<artifact_dir>/network_scan.json` | network_scan |
| `<artifact_dir>/traffic_analyze.json` | traffic_analyze |
| `<artifact_dir>/dissector_created.json` | dissector_create |
| `<artifact_dir>/tool_recommendations.json` | tool_recommend |
| `<artifact_dir>/<dissector_name>.lua` | dissector_create (generated Lua file) |

## ScapyAdapter (Python Module)

Located at `src/general_ludd/networking/scapy_adapter.py`. Provides structured
Python return types for all packet operations.

```python
def read_pcap(path: str, filter_str: str = "") -> list[PacketSummary]:
    """Read and summarize a pcap file."""

def craft_packet(protocol_stack: list[str], fields: dict) -> dict:
    """Build packet specification from protocol stack and field definitions."""

def send_packet(packet_spec: dict, interface: str = "eth0", count: int = 1) -> dict:
    """Send crafted packets on specified interface. Returns send result."""

def analyze_pcap(path: str, filter_str: str = "") -> TrafficReport:
    """Analyze pcap traffic: protocol distribution, top talkers, top ports,
    flow analysis, capture duration."""

def dissect_packet(path_or_bytes: str | bytes) -> list[dict]:
    """Dissect raw bytes or pcap into protocol layers."""

def sniff_packets(interface: str, count: int, timeout: int, filter_str: str = "") -> list[PacketSummary]:
    """Live capture packets on interface."""
```

The `general_ludd.agent.gludd_scapy` Ansible module wraps the adapter with
YAML-friendly inputs: `read_pcap`, `write_pcap`, `craft_packet`, `send_packet`,
`sniff_packets`, `analyze_pcap`, `dissect_packet`.

## Tool Awareness Matrix

Which external networking tools the role knows about and can coordinate with:

| Tool | Category | Known scenarios |
|---|---|---|
| tshark / tcpdump | Packet capture | pcap read, traffic analysis, live capture |
| nmap | Network discovery | port scan, service enumeration, OS detection |
| zeek (Bro) | Traffic analysis | protocol analysis, connection logging |
| hping3 | Packet craft | custom packet generation, firewall testing |
| tc (traffic control) | Traffic shaping | latency injection, bandwidth limit, packet loss |
| iperf3 | Bandwidth | throughput measurement, jitter, packet loss |
| dig / nslookup | DNS | DNS resolution, record types, DNSSEC |
| openssl s_client | TLS | TLS handshake, certificate chain, cipher suites |
| arp-scan / arping | ARP | ARP table enumeration, IP conflict detection |
| mtr / traceroute | Routing | path analysis, per-hop latency |
| curl / wget | HTTP | HTTP response headers, redirect chains |
| netstat / ss | Connection state | socket state, listening ports, connections |

## Dissector Templates

The role ships two Wireshark Lua dissector files in `files/`:

- **`dissector_template.lua`** (212 lines): Full scaffold with field registration,
  subtrees, expert info handlers, heuristic detection, and TCP reassembly support.
- **`dissector_example.lua`** (228 lines): Example GPS protocol dissector showing
  how to use the template with a real protocol.

## Usage Examples

### Packet Analysis Workflow

```yaml
- name: Read and analyze a packet capture
  hosts: localhost
  vars:
    networking__mode: pcap_read
    networking__pcap_path: /tmp/capture.pcap
  roles:
    - role: general_ludd.networking.networking
```

Produces `pcap_read.json` with protocol hierarchy, packet counts, and structured
packet summaries.

```yaml
- name: Full traffic analysis of a pcap
  hosts: localhost
  vars:
    networking__mode: traffic_analyze
    networking__pcap_path: /tmp/capture.pcap
  roles:
    - role: general_ludd.networking.networking
```

Writes `traffic_analyze.json` with protocol distribution, top talkers, top ports,
flow analysis, and capture duration.

### Network Discovery Workflow

```yaml
- name: Scan a target subnet for open ports
  hosts: localhost
  vars:
    networking__mode: network_scan
    networking__scan_target: 192.168.1.0/24
    networking__scan_ports: "22,80,443,3306,5432"
    networking__scan_type: version
  roles:
    - role: general_ludd.networking.networking
```

Writes `network_scan.json` with host discovery results, open ports, and service
version information.

### Dissector Creation Workflow

```yaml
- name: Generate a Wireshark Lua dissector for a custom protocol
  hosts: localhost
  vars:
    networking__mode: dissector_create
    networking__dissector_name: "MyProtocol"
    networking__dissector_language: lua
    networking__dissector_port: 9000
    networking__dissector_fields:
      - {name: "msg_type", type: "uint8", description: "Message type", offset: 0, size: 1}
      - {name: "msg_length", type: "uint16", description: "Message length", offset: 1, size: 2}
      - {name: "payload", type: "bytes", description: "Message payload", offset: 3, size: 0}
  roles:
    - role: general_ludd.networking.networking
```

Generates `MyProtocol.lua` dissector file and `dissector_created.json` metadata
in the artifact directory.

### Diagnostic Tool Recommendation

```yaml
- name: Recommend tools for TLS handshake failure diagnosis
  hosts: localhost
  vars:
    networking__mode: tool_recommend
    networking__diagnostic_scenario: tls_error
    networking__tool_context: "api.example.com:443"
  roles:
    - role: general_ludd.networking.networking
```

Returns the top 2-3 tools with command examples matched to the diagnostic scenario.

## Security

- `networking__psk` is `no_log: true` on every task accessing the daemon
- Artifact files written with mode `0644` to `networking__artifact_dir`
- Packet capture and sniff operations run only when `not ansible_check_mode`
- Raw packet data payloads are truncated to 64 bytes in artifact output
- nmap scan operations respect `ansible_check_mode` — dry-run safe by default
