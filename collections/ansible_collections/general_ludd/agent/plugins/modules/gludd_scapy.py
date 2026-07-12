#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_scapy
  short_description: Packet crafting, sniffing, and pcap manipulation via Scapy
  description:
    - Wraps the C(general_ludd.networking.scapy_adapter) so networking playbooks
      can craft, send, sniff, and analyze packets directly.
    - Read-only actions (C(read_pcap), C(analyze_pcap), C(dissect_packet)) are
      check-mode safe and return C(changed=False).
    - Mutating actions (C(write_pcap), C(craft_packet), C(send_packet),
      C(sniff_packets)) require the adapter binary (C(scapy)) and return
      C(changed=True) on success.
    - All actions run via the adapter's Python API (never C(shell=True)).
  options:
    action:
      description: The Scapy operation to perform.
      type: str
      required: true
      choices:
        - read_pcap
        - write_pcap
        - craft_packet
        - send_packet
        - sniff_packets
        - analyze_pcap
        - dissect_packet
    pcap_path:
      description: Filesystem path to a pcap file for read/write/analyze actions.
      type: str
    packets:
      description: List of packet dicts for C(craft_packet) batch construction.
      type: list
      elements: dict
    protocol_stack:
      description: Ordered protocol layers for C(craft_packet), e.g. C(["Ether", "IP", "TCP"]).
      type: list
      elements: str
    packet_fields:
      description: Per-layer field overrides for C(craft_packet) or C(dissect_packet).
      type: dict
    interface:
      description: Network interface for C(send_packet) or C(sniff_packets).
      type: str
      default: eth0
    count:
      description: Packet count for C(sniff_packets) or repeat count for C(send_packet).
      type: int
      default: 1
    timeout:
      description: Sniff timeout in seconds.
      type: int
      default: 30
    output_format:
      description: Output format for C(dissect_packet) — C(json) or C(hex).
      type: str
      default: json
      choices:
        - json
        - hex

EXAMPLES:
  - name: Decode a packet from hex
    general_ludd.agent.gludd_scapy:
      action: dissect_packet
      packet_fields:
        raw_hex: "00010203040506deadbeef00010800..."
    register: decoded

  - name: Craft an ICMP echo request
    general_ludd.agent.gludd_scapy:
      action: craft_packet
      protocol_stack: ["Ether", "IP", "ICMP"]
      packet_fields:
        IP:
          dst: "8.8.8.8"
        ICMP:
          type: 8
    register: crafted

  - name: Sniff DNS queries on eth0 for 10s
    general_ludd.agent.gludd_scapy:
      action: sniff_packets
      interface: eth0
      count: 5
      timeout: 10
    register: sniffed

  - name: Read and summarize a pcap
    general_ludd.agent.gludd_scapy:
      action: analyze_pcap
      pcap_path: /tmp/capture.pcap
    register: analysis

RETURN:
  result:
    description: Action-specific result payload.
    type: dict
    returned: always
    contains:
      action:
        description: The action that was executed.
        type: str
      output:
        description: Action-specific output (packet hex/dicts, sniffed packets, analysis summary).
        type: raw
      summary:
        description: Human-readable summary when applicable.
        type: str
"""

from __future__ import annotations

import os

from ansible.module_utils.basic import AnsibleModule  # type: ignore[import]

try:
    from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
        error_result,
        ok_result,
    )
except ImportError:
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "module_utils"))
    from gludd import error_result, ok_result  # type: ignore[import]


_READ_ONLY_ACTIONS = frozenset({"read_pcap", "analyze_pcap", "dissect_packet"})
_MUTATING_ACTIONS = frozenset({"write_pcap", "craft_packet", "send_packet", "sniff_packets"})


def _get_adapter():
    try:
        from general_ludd.networking.scapy_adapter import ScapyAdapter  # type: ignore[import]

        return ScapyAdapter()
    except ImportError:
        return None


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            action=dict(
                type="str",
                required=True,
                choices=sorted(_READ_ONLY_ACTIONS | _MUTATING_ACTIONS),
            ),
            pcap_path=dict(type="str"),
            packets=dict(type="list", elements="dict"),
            protocol_stack=dict(type="list", elements="str"),
            packet_fields=dict(type="dict"),
            interface=dict(type="str", default="eth0"),
            count=dict(type="int", default=1),
            timeout=dict(type="int", default=30),
            output_format=dict(type="str", default="json", choices=["json", "hex"]),
        ),
        supports_check_mode=True,
    )

    action: str = module.params["action"]
    pcap_path: str | None = module.params["pcap_path"]
    packets: list | None = module.params["packets"]
    protocol_stack: list | None = module.params["protocol_stack"]
    packet_fields: dict | None = module.params["packet_fields"]
    interface: str = module.params["interface"]
    count: int = module.params["count"]
    timeout: int = module.params["timeout"]
    output_format: str = module.params["output_format"]

    adapter = _get_adapter()
    if adapter is None:
        module.fail_json(
            **error_result(
                "Scapy adapter not available — "
                "ensure general_ludd.networking.scapy_adapter is installed "
                "(scapy Python package required)"
            )
        )
        return

    is_read_only = action in _READ_ONLY_ACTIONS

    if module.check_mode and is_read_only:
        module.exit_json(
            **ok_result(
                {
                    "action": action,
                    "output": None,
                    "summary": f"check_mode: would run {action}",
                },
                changed=False,
            )
        )
        return

    if module.check_mode:
        module.exit_json(
            **ok_result(
                {
                    "action": action,
                    "output": None,
                    "summary": f"check_mode: would run {action} (mutating)",
                },
                changed=False,
            )
        )
        return

    try:
        if action == "read_pcap":
            if not pcap_path:
                module.fail_json(**error_result("pcap_path is required for read_pcap"))
                return
            result = adapter.read_pcap(pcap_path)
            module.exit_json(
                **ok_result(
                    {"action": action, "output": result, "summary": f"read {len(result)} packets from {pcap_path}"},
                    changed=False,
                )
            )

        elif action == "write_pcap":
            if not pcap_path or not packets:
                module.fail_json(**error_result("pcap_path and packets are required for write_pcap"))
                return
            adapter.write_pcap(pcap_path, packets)
            module.exit_json(
                **ok_result(
                    {"action": action, "output": {"path": pcap_path, "count": len(packets)}},
                    changed=True,
                )
            )

        elif action == "craft_packet":
            if not protocol_stack:
                module.fail_json(**error_result("protocol_stack is required for craft_packet"))
                return
            pkt = adapter.craft_packet(protocol_stack, packet_fields or {})
            module.exit_json(
                **ok_result(
                    {"action": action, "output": pkt},
                    changed=True,
                )
            )

        elif action == "send_packet":
            if not protocol_stack:
                module.fail_json(**error_result("protocol_stack is required for send_packet"))
                return
            status = adapter.send_packet(protocol_stack, packet_fields or {}, interface, count)
            module.exit_json(
                **ok_result(
                    {"action": action, "output": status, "summary": f"sent {count} packet(s) on {interface}"},
                    changed=True,
                )
            )

        elif action == "sniff_packets":
            pkts = adapter.sniff_packets(interface=interface, count=count, timeout=timeout)
            module.exit_json(
                **ok_result(
                    {"action": action, "output": pkts, "summary": f"sniffed {len(pkts)} packet(s) on {interface}"},
                    changed=True,
                )
            )

        elif action == "analyze_pcap":
            if not pcap_path:
                module.fail_json(**error_result("pcap_path is required for analyze_pcap"))
                return
            analysis = adapter.analyze_pcap(pcap_path)
            module.exit_json(
                **ok_result(
                    {"action": action, "output": analysis, "summary": f"analyzed {pcap_path}"},
                    changed=False,
                )
            )

        elif action == "dissect_packet":
            if not packet_fields:
                module.fail_json(**error_result("packet_fields is required for dissect_packet"))
                return
            dissected = adapter.dissect_packet(packet_fields, output_format=output_format)
            module.exit_json(
                **ok_result(
                    {"action": action, "output": dissected},
                    changed=False,
                )
            )

        else:
            module.fail_json(**error_result(f"unknown action: {action}"))

    except Exception as exc:
        module.fail_json(**error_result(f"scapy action '{action}' failed: {exc}"))


if __name__ == "__main__":
    main()
