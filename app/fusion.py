"""Garmin Fusion marine stereo protocol over NMEA 2000.

Commands are proprietary PGN 126720 and status messages are PGN 130820. Both
start with the same 2-byte header (manufacturer 419 = Fusion Electronics,
industry 4 = Marine) followed by a 16-bit little-endian message id.

Message ids and field layouts come from the open-source canboat PGN
database (canboat.json, "Fusion: ..." entries). They are reverse-engineered,
not an official Garmin spec, and only the subset this dashboard uses is
implemented: power, source select, play/pause/next/prev, mute, zone volume,
and status parsing for what is playing, the zones' volumes, limits and names.

Zones: the stereo's user interface counts zones from 1, but the zone byte in commands
(and in the zone-name status) counts from 0: wire 0 is Zone 1. That is what canboat's
captures show and what the signalk-fusion-stereo plugin sends (zone number minus one).
The four-slot status messages (volumes, volume limits) list Zone 1 first. The functions
here take the number the user sees (1 = Zone 1) and do the conversion.
"""
import struct

PGN_COMMAND = 126720
PGN_STATUS = 130820

HEADER = bytes([0xA3, 0x99])  # 419 | reserved 0b11 << 11 | industry 4 << 13, little-endian

MSG_REQUEST_STATUS = 1
MSG_SET_SOURCE = 2
MSG_MEDIA_COMMAND = 3
MSG_SET_MUTE = 17
MSG_SET_ZONE_VOLUME = 24
MSG_POWER = 28

MEDIA_PLAY = 1
MEDIA_PAUSE = 2
MEDIA_NEXT = 4
MEDIA_PREV = 6

ST_SOURCE = 32770
ST_MEDIA = 32772
ST_TRACK_TITLE = 32773
ST_TRACK_ARTIST = 32774
ST_TRACK_ALBUM = 32775
ST_TRACK_PROGRESS = 32777
ST_MUTE = 32791
ST_VOLUME_LIMITS = 32796
ST_VOLUME = 32797
ST_POWER = 32800
ST_ZONE_NAME = 32813

SOURCE_TYPES = {
    0: "AM", 1: "FM", 2: "Aux", 3: "Sirius", 4: "iPod", 5: "USB", 6: "DVD", 7: "VHF", 9: "MTP",
    10: "Bluetooth", 11: "ARC", 12: "Android", 13: "Pandora", 14: "DAB", 15: "AirPlay", 16: "UPNP",
}
PLAY_STATUS = {1: "playing", 2: "paused", 3: "stopped", 4: "skip_forward", 5: "skip_rewind"}


def _command(message_id, *args):
    return HEADER + struct.pack("<H", message_id) + bytes(args)


def request_status():
    return _command(MSG_REQUEST_STATUS)


def set_source(source_id):
    return _command(MSG_SET_SOURCE, source_id)


def media_command(source_id, command):
    return _command(MSG_MEDIA_COMMAND, source_id, command)


def set_mute(on):
    return _command(MSG_SET_MUTE, 1 if on else 2)


def set_zone_volume(zone, volume):
    """zone is the number shown on the stereo (1 = Zone 1); the wire wants it zero-based."""
    if not 1 <= zone <= 255:
        raise ValueError("zone numbers start at 1")
    return _command(MSG_SET_ZONE_VOLUME, zone - 1, volume)


def set_power(on):
    return _command(MSG_POWER, 1 if on else 2)


def is_fusion(payload):
    return len(payload) >= 4 and payload[:2] == HEADER


def _string_lz(data, offset):
    """Length-prefixed, zero-terminated string; tolerant of the length counting the terminator or not."""
    if offset >= len(data):
        return ""
    raw = data[offset + 1: offset + 1 + data[offset]]
    return raw.split(b"\x00")[0].decode("utf-8", errors="replace")


def parse_status(payload):
    """Parse a PGN 130820 payload into a dict of state updates, or None if it isn't one we understand."""
    if not is_fusion(payload):
        return None
    message_id = struct.unpack_from("<H", payload, 2)[0]

    try:
        if message_id == ST_POWER and len(payload) >= 5:
            return {"power": payload[4] == 1}
        if message_id == ST_MUTE and len(payload) >= 5:
            return {"muted": payload[4] == 1}
        if message_id == ST_VOLUME and len(payload) >= 8:
            return {"volumes": list(payload[4:8])}
        if message_id == ST_VOLUME_LIMITS and len(payload) >= 8:
            return {"volume_limits": list(payload[4:8])}
        if message_id == ST_ZONE_NAME and len(payload) >= 6:
            name = _string_lz(payload, 5)
            return {"zone_name": {"zone": payload[4] + 1, "name": name}} if name else None   # the wire index is zero-based
        if message_id == ST_SOURCE and len(payload) >= 8:
            source_id, current_id, source_type = payload[4], payload[5], payload[6]
            name = _string_lz(payload, 8) or SOURCE_TYPES.get(source_type, "Source %d" % source_id)
            return {
                "source_entry": {"id": source_id, "name": name, "type": SOURCE_TYPES.get(source_type, "Unknown")},
                "source_id": current_id,
            }
        if message_id == ST_MEDIA and len(payload) >= 23:
            track, track_count, length_ms, position_ms = struct.unpack_from("<IIII", payload, 7)
            return {
                "source_id": payload[4],
                "play_status": PLAY_STATUS.get(payload[5], "stopped"),
                "track": track,
                "track_count": track_count,
                "length_s": length_ms / 1000.0,
                "position_s": position_ms / 1000.0,
            }
        if message_id in (ST_TRACK_TITLE, ST_TRACK_ARTIST, ST_TRACK_ALBUM) and len(payload) >= 10:
            key = {ST_TRACK_TITLE: "title", ST_TRACK_ARTIST: "artist", ST_TRACK_ALBUM: "album"}[message_id]
            return {key: _string_lz(payload, 9)}
        if message_id == ST_TRACK_PROGRESS and len(payload) >= 8:
            progress_ms = int.from_bytes(payload[5:8], "little")
            return {"position_s": progress_ms / 1000.0}
    except (struct.error, IndexError):
        return None
    return None
