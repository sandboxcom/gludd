-- =============================================================================
-- Wireshark Lua Dissector Template
-- =============================================================================
-- Usage: wireshark -X lua_script:dissector_template.lua
-- Copy to ~/.local/lib/wireshark/plugins/ for auto-loading
-- See: https://wiki.wireshark.org/Lua/Dissectors
-- =============================================================================

-- 1. Protocol Definition ----------------------------------------------------
local PROTO_NAME = "myproto"
local PROTO_LONG = "My Custom Protocol"

local proto = Proto(PROTO_NAME, PROTO_LONG)

-- 2. Field Definitions ------------------------------------------------------
-- Integer fields: uint8, uint16, uint32, uint64 (and signed variants)
local f_magic    = ProtoField.uint32(PROTO_NAME .. ".magic",    "Magic Number",   base.HEX)
local f_version   = ProtoField.uint8( PROTO_NAME .. ".version",  "Version",        base.DEC)
local f_cmd       = ProtoField.uint8( PROTO_NAME .. ".cmd",      "Command",        base.DEC, nil, 0xF0)
local f_subcmd    = ProtoField.uint8( PROTO_NAME .. ".subcmd",   "Sub-Command",    base.DEC, nil, 0x0F)
local f_length    = ProtoField.uint16(PROTO_NAME .. ".length",   "Payload Length", base.DEC)
local f_flags     = ProtoField.uint16(PROTO_NAME .. ".flags",    "Flags",          base.HEX)
local f_seq       = ProtoField.uint32(PROTO_NAME .. ".seq",      "Sequence Number", base.DEC)
local f_timestamp = ProtoField.uint64(PROTO_NAME .. ".timestamp","Timestamp",      base.DEC)
local f_src_ip    = ProtoField.ipv4(  PROTO_NAME .. ".src_ip",   "Source IP")
local f_dst_ip    = ProtoField.ipv4(  PROTO_NAME .. ".dst_ip",   "Destination IP")
local f_src_mac   = ProtoField.ether( PROTO_NAME .. ".src_mac",  "Source MAC")
local f_dst_mac   = ProtoField.ether( PROTO_NAME .. ".dst_mac",  "Destination MAC")
local f_payload   = ProtoField.string(PROTO_NAME .. ".payload",  "Payload",         base.ASCII)
local f_raw_data  = ProtoField.bytes( PROTO_NAME .. ".raw_data", "Raw Data")
local f_crc       = ProtoField.uint16(PROTO_NAME .. ".crc",      "CRC-16",          base.HEX)

-- 3. Bitmask Sub-Fields (composite fields) ----------------------------------
local f_cmd_val  = ProtoField.uint8(PROTO_NAME .. ".cmd_val",  "Command Code", base.DEC)
local f_cmd_type = ProtoField.uint8(PROTO_NAME .. ".cmd_type", "Command Type", base.DEC)

proto.fields = {
    f_magic, f_version, f_cmd, f_subcmd, f_length, f_flags, f_seq,
    f_timestamp, f_src_ip, f_dst_ip, f_src_mac, f_dst_mac,
    f_payload, f_raw_data, f_crc,
    f_cmd_val, f_cmd_type,
}

-- 4. Expert Info Registration -----------------------------------------------
local expert_malformed = ProtoExpert.new(PROTO_NAME .. ".malformed.expert",
    "Malformed packet", expert.group.MALFORMED, expert.severity.ERROR)
local expert_unexpected = ProtoExpert.new(PROTO_NAME .. ".unexpected.expert",
    "Unexpected value", expert.group.PROTOCOL, expert.severity.WARN)
local expert_note = ProtoExpert.new(PROTO_NAME .. ".note.expert",
    "Protocol note", expert.group.PROTOCOL, expert.severity.NOTE)

proto.experts = { expert_malformed, expert_unexpected, expert_note }

-- 5. Preference / Configuration Support -------------------------------------
proto.prefs.udp_port = Pref.uint("UDP port", 12345,
    "UDP port number for this protocol")
proto.prefs.tcp_port = Pref.uint("TCP port", 12345,
    "TCP port number for this protocol")
proto.prefs.enable_heuristic = Pref.bool("Heuristic dissector", true,
    "Enable heuristic dissector (tries to detect on any port)")

-- 6. Dissector Function -----------------------------------------------------
function proto.dissector(buffer, pinfo, tree)
    -- Minimal length check: protocol header is 4 bytes minimum
    if buffer:len() < 4 then
        return 0
    end

    pinfo.cols.protocol:set(PROTO_NAME)

    -- 7. Subtree Management -------------------------------------------------
    local subtree = tree:add(proto, buffer(), PROTO_LONG .. " Header")

    -- Magic number
    local field_offset = 0
    local magic = buffer(field_offset, 4):uint()
    subtree:add(f_magic, buffer(field_offset, 4))

    -- Minimal validation with expert info
    if magic ~= 0xDEADBEEF then
        subtree:add_proto_expert_info(expert_unexpected, "Magic value 0x" ..
            string.format("%08X", magic) .. " != expected 0xDEADBEEF")
    end

    -- Version
    field_offset = field_offset + 4
    subtree:add(f_version, buffer(field_offset, 1))

    -- Command with bitmask
    field_offset = field_offset + 1
    local cmd_byte = buffer(field_offset, 1):uint()
    subtree:add(f_cmd, buffer(field_offset, 1))

    local cmd_tree = subtree:add(f_cmd, buffer(field_offset, 1))
    cmd_tree:add(f_cmd_val, buffer(field_offset, 1), bit.band(cmd_byte, 0xF0))
    cmd_tree:add(f_cmd_type, buffer(field_offset, 1), bit.band(cmd_byte, 0x0F))

    -- Length
    field_offset = field_offset + 1
    local payload_len = buffer(field_offset, 2):uint()
    subtree:add(f_length, buffer(field_offset, 2))

    -- Length validation
    local expected_total = (field_offset + 2) + payload_len + 2
    if buffer:len() ~= expected_total then
        subtree:add_proto_expert_info(expert_malformed,
            string.format("Length mismatch: buffer=%d expected=%d",
                buffer:len(), expected_total))
    end

    -- Flags
    field_offset = field_offset + 2
    subtree:add(f_flags, buffer(field_offset, 2))

    -- Sequence number
    field_offset = field_offset + 2
    subtree:add(f_seq, buffer(field_offset, 4))

    -- Timestamp
    field_offset = field_offset + 4
    subtree:add(f_timestamp, buffer(field_offset, 8))

    -- IPv4 fields
    field_offset = field_offset + 8
    subtree:add(f_src_ip, buffer(field_offset, 4))
    subtree:add(f_dst_ip, buffer(field_offset + 4, 4))

    -- MAC fields
    field_offset = field_offset + 8
    subtree:add(f_src_mac, buffer(field_offset, 6))
    subtree:add(f_dst_mac, buffer(field_offset + 6, 6))

    -- Payload (variable length)
    field_offset = field_offset + 12
    if payload_len > 0 then
        subtree:add(f_payload, buffer(field_offset, payload_len))
    end

    -- Raw bytes view
    subtree:add(f_raw_data, buffer())

    -- CRC
    local crc_offset = field_offset + payload_len
    subtree:add(f_crc, buffer(crc_offset, 2))

    subtree:add_proto_expert_info(expert_note, "Dissected by template")

    return buffer:len()
end

-- 8. Heuristic Dissector Registration ---------------------------------------
local function heuristic_checker(buffer, pinfo, tree)
    if buffer:len() < 4 then
        return false
    end

    local magic = buffer(0, 4):uint()
    if magic == 0xDEADBEEF then
        proto.dissector(buffer, pinfo, tree)
        return true
    end

    return false
end

proto:register_heuristic("udp", heuristic_checker)
proto:register_heuristic("tcp", heuristic_checker)

-- 9. Port Dissector Registration --------------------------------------------
local function reapply_prefs()
    local udp_table = DissectorTable.get("udp.port")
    local tcp_table = DissectorTable.get("tcp.port")

    udp_table:remove(proto.prefs.udp_port, proto)
    tcp_table:remove(proto.prefs.tcp_port, proto)

    udp_table:add(proto.prefs.udp_port, proto)
    tcp_table:add(proto.prefs.tcp_port, proto)

    if proto.prefs.enable_heuristic then
        proto:register_heuristic("udp", heuristic_checker)
        proto:register_heuristic("tcp", heuristic_checker)
    end
end

proto.prefs_changed = reapply_prefs

-- 10. Reassembly (TCP Stream) ------------------------------------------------
--
-- For protocols that span multiple TCP segments, use tcp_dissect_pdus():
--
--   local function get_pdu_length(buffer, pinfo, tree)
--       if buffer:len() < 6 then
--           return 0
--       end
--       local length_field = buffer(4, 2):uint()
--       return length_field + 6
--   end
--
--   function proto.dissector(buffer, pinfo, tree)
--       local dissector_func = function(tvb, pinfo, tree)
--           -- actual dissector logic here
--       end
--       tcp_dissect_pdus(buffer, tree, 6, get_pdu_length, dissector_func)
--   end
--
-- Then register as a TCP subdissector:
--   local tcp_port = DissectorTable.get("tcp.port")
--   tcp_port:add(12345, proto)

-- Initial registration on script load
reapply_prefs()
