-- =============================================================================
-- Wireshark Dissector Example: Simple Binary GPS Protocol
-- =============================================================================
-- Protocol format (big-endian):
-- +--------+--------+--------+--------+--------+--------+--------+--------+
-- | Magic (32)       | Ver(8) | Flags(8)| Seq(16) | Length(16)           |
-- +--------+--------+--------+--------+--------+--------+--------+--------+
-- | Timestamp (64)                                        | Lat(32)       |
-- +--------+--------+--------+--------+--------+--------+--------+--------+
-- | Lon(32)           | Alt(16) | NSats(8)| Fix(8) | CRC(16)              |
-- +--------+--------+--------+--------+--------+--------+--------------------+
-- | Payload (Length bytes)                                                   |
-- +--------+--------+--------+--------+--------+--------+--------+--------+
-- Total header (fixed): 36 bytes + variable payload + 2 byte CRC at end
-- =============================================================================

local proto = Proto("gpsproto", "GPS Binary Protocol")

-- Header fields
local f_magic     = ProtoField.uint32("gpsproto.magic",     "Magic",      base.HEX)
local f_version   = ProtoField.uint8( "gpsproto.version",   "Version",    base.DEC)
local f_flags     = ProtoField.uint8( "gpsproto.flags",     "Flags",      base.HEX)
local f_seq       = ProtoField.uint16("gpsproto.seq",       "Sequence",   base.DEC)
local f_length    = ProtoField.uint16("gpsproto.length",    "Length",     base.DEC)
local f_timestamp = ProtoField.uint64("gpsproto.timestamp", "Timestamp",  base.DEC)
local f_lat       = ProtoField.int32( "gpsproto.lat",       "Latitude",   base.DEC)
local f_lon       = ProtoField.int32( "gpsproto.lon",       "Longitude",  base.DEC)
local f_alt       = ProtoField.uint16("gpsproto.alt",       "Altitude",   base.DEC)
local f_nsats     = ProtoField.uint8( "gpsproto.nsats",     "Satellites", base.DEC)
local f_fix       = ProtoField.uint8( "gpsproto.fix",       "Fix Type",   base.DEC,
    { [0] = "No Fix", [1] = "2D", [2] = "3D" })
local f_payload   = ProtoField.string("gpsproto.payload",   "Payload",    base.ASCII)
local f_crc       = ProtoField.uint16("gpsproto.crc",       "CRC-16",     base.HEX)

-- Flag sub-fields
local f_flag_encrypted  = ProtoField.bool( "gpsproto.flags.encrypted",  "Encrypted",  8, nil, 0x01)
local f_flag_acked      = ProtoField.bool( "gpsproto.flags.acked",      "Acked",      8, nil, 0x02)
local f_flag_retransmit = ProtoField.bool( "gpsproto.flags.retransmit", "Retransmit", 8, nil, 0x04)
local f_flag_reserved   = ProtoField.uint8( "gpsproto.flags.reserved",  "Reserved",   base.HEX, nil, 0xF8)

proto.fields = {
    f_magic, f_version, f_flags, f_seq, f_length, f_timestamp,
    f_lat, f_lon, f_alt, f_nsats, f_fix, f_payload, f_crc,
    f_flag_encrypted, f_flag_acked, f_flag_retransmit, f_flag_reserved,
}

-- Expert info
local ei_magic_bad   = ProtoExpert.new("gpsproto.magic.error",    "Invalid magic number",
    expert.group.MALFORMED, expert.severity.ERROR)
local ei_crc_bad     = ProtoExpert.new("gpsproto.crc.error",      "CRC mismatch",
    expert.group.CHECKSUM, expert.severity.ERROR)
local ei_length_bad  = ProtoExpert.new("gpsproto.length.error",   "Length mismatch",
    expert.group.MALFORMED, expert.severity.ERROR)
local ei_low_sats    = ProtoExpert.new("gpsproto.sats.warn",      "Low satellite count",
    expert.group.PROTOCOL, expert.severity.WARN)
local ei_retransmit  = ProtoExpert.new("gpsproto.retransmit.note","Retransmitted packet",
    expert.group.PROTOCOL, expert.severity.NOTE)

proto.experts = { ei_magic_bad, ei_crc_bad, ei_length_bad, ei_low_sats, ei_retransmit }

-- Preferences
proto.prefs.udp_port = Pref.uint("UDP port", 25250, "UDP port for GPS protocol")
proto.prefs.tcp_port = Pref.uint("TCP port", 25250, "TCP port for GPS protocol")
proto.prefs.enable_heuristic = Pref.bool("Heuristic dissector", true,
    "Enable heuristic detection (magic 0x47505331)")

-- CRC-16 CCITT
local CRC_TABLE = {}
for i = 0, 255 do
    local crc = i
    for _ = 1, 8 do
        crc = (crc >> 1) ~ ((crc & 1) == 1 and 0x8408 or 0)
    end
    CRC_TABLE[i] = crc
end

local function crc16_ccitt(data, offset, length)
    local crc = 0xFFFF
    for i = offset, offset + length - 1 do
        crc = CRC_TABLE[bit.bxor(crc, data(i, 1):uint()) % 256] ~ (crc >> 8)
    end
    return bit.bxor(crc, 0xFFFF)
end

-- Dissector
function proto.dissector(buffer, pinfo, tree)
    if buffer:len() < 36 then
        return 0
    end

    pinfo.cols.protocol:set("GPS-PROTO")

    local subtree = tree:add(proto, buffer(), "GPS Binary Protocol")

    local offset = 0

    -- Magic (4 bytes)
    local magic_val = buffer(offset, 4):uint()
    subtree:add(f_magic, buffer(offset, 4))
    if magic_val ~= 0x47505331 then
        subtree:add_proto_expert_info(ei_magic_bad,
            string.format("Expected 0x47505331, got 0x%08X", magic_val))
    end
    offset = offset + 4

    -- Version (1 byte)
    subtree:add(f_version, buffer(offset, 1))
    offset = offset + 1

    -- Flags with bitmask sub-fields
    local flags_val = buffer(offset, 1):uint()
    local flags_tree = subtree:add(f_flags, buffer(offset, 1))
    flags_tree:add(f_flag_encrypted, buffer(offset, 1))
    flags_tree:add(f_flag_acked, buffer(offset, 1))
    flags_tree:add(f_flag_retransmit, buffer(offset, 1))
    flags_tree:add(f_flag_reserved, buffer(offset, 1))

    if bit.band(flags_val, 0x04) ~= 0 then
        subtree:add_proto_expert_info(ei_retransmit)
    end
    offset = offset + 1

    -- Sequence (2 bytes)
    subtree:add(f_seq, buffer(offset, 2))
    offset = offset + 2

    -- Payload length (2 bytes)
    local payload_len = buffer(offset, 2):uint()
    subtree:add(f_length, buffer(offset, 2))
    offset = offset + 2

    -- Timestamp (8 bytes)
    subtree:add(f_timestamp, buffer(offset, 8))
    offset = offset + 8

    -- Latitude (4 bytes, signed, microdegrees)
    local lat_raw = buffer(offset, 4):int()
    subtree:add(f_lat, buffer(offset, 4))
    pinfo.cols.info:append(string.format(" Lat=%.6f", lat_raw / 1e6))
    offset = offset + 4

    -- Longitude (4 bytes, signed, microdegrees)
    local lon_raw = buffer(offset, 4):int()
    subtree:add(f_lon, buffer(offset, 4))
    pinfo.cols.info:prepend(string.format("GPS "))
    pinfo.cols.info:append(string.format(" Lon=%.6f", lon_raw / 1e6))
    offset = offset + 4

    -- Altitude (2 bytes)
    subtree:add(f_alt, buffer(offset, 2))
    offset = offset + 2

    -- Satellite count (1 byte)
    local nsats = buffer(offset, 1):uint()
    subtree:add(f_nsats, buffer(offset, 1))
    if nsats < 4 then
        subtree:add_proto_expert_info(ei_low_sats,
            string.format("Only %d satellites in view", nsats))
    end
    offset = offset + 1

    -- Fix type (1 byte)
    subtree:add(f_fix, buffer(offset, 1))
    offset = offset + 1

    -- CRC (2 bytes) -- placed after fix, before payload
    local crc_offset = offset
    subtree:add(f_crc, buffer(offset, 2))
    offset = offset + 2

    -- Payload (variable)
    if payload_len > 0 and (36 + payload_len) <= buffer:len() then
        subtree:add(f_payload, buffer(offset, payload_len))
    end
    offset = offset + payload_len

    -- Validate total length
    local expected_len = 34 + payload_len + 2
    if buffer:len() > expected_len then
        subtree:add_proto_expert_info(ei_length_bad,
            string.format("Extra data: buffer=%d expected=%d", buffer:len(), expected_len))
    end

    -- Validate CRC
    local computed_crc = crc16_ccitt(buffer, 0, crc_offset)
    local wire_crc = buffer(crc_offset, 2):uint()
    -- Note: crc_offset + 2 bytes CRC span; CRC over header up to CRC field
    local computed = crc16_ccitt(buffer, 0, crc_offset)
    if computed ~= wire_crc then
        subtree:add_proto_expert_info(ei_crc_bad,
            string.format("Computed 0x%04X != wire 0x%04X", computed, wire_crc))
    end

    return buffer:len()
end

-- Heuristic dissector
local function heuristic_checker(buffer, pinfo, tree)
    if buffer:len() < 4 then
        return false
    end
    local magic = buffer(0, 4):uint()
    if magic == 0x47505331 then
        proto.dissector(buffer, pinfo, tree)
        return true
    end
    return false
end

proto:register_heuristic("udp", heuristic_checker)
proto:register_heuristic("tcp", heuristic_checker)

-- Port registration with preferences support
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
reapply_prefs()
