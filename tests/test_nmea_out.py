"""NMEA 0183 out (app/nmea_out.py): what a chart app reads the boat's position and depth from."""
import asyncio
import calendar

from app.nmea_out import NmeaServer, checksum, sentence, sentences

T = calendar.timegm((2026, 9, 24, 14, 5, 9))
FIX = {"lat": 36.10125, "lon": -85.8275, "sog_kn": 6.2, "cog_deg": 271.4, "heading_deg": 268.0,
       "satellites": 9, "hdop": 0.9, "fix_quality": 1, "has_fix": True, "timestamp": T}


def by_type(lines):
    return {ln[3:6]: ln for ln in lines}


def test_checksum_matches_the_standards_example():
    assert checksum("GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W") == "6A"


def test_sentence_framing():
    assert sentence("GPVTG,1.0,T,,M,2.0,N,3.7,K,A") == "$GPVTG,1.0,T,,M,2.0,N,3.7,K,A*%s\r\n" % checksum(
        "GPVTG,1.0,T,,M,2.0,N,3.7,K,A")


def test_position_speed_and_course():
    s = by_type(sentences(FIX, {}))
    assert s["RMC"].startswith("$GPRMC,140509.00,A,3606.0750,N,08549.6500,W,6.2,271.4,240926,,,A*")
    assert s["GGA"].startswith("$GPGGA,140509.00,3606.0750,N,08549.6500,W,1,09,0.9,,M,,M,,*")
    assert s["VTG"].startswith("$GPVTG,271.4,T,,M,6.2,N,11.5,K,A*")
    assert s["HDM"].startswith("$HCHDM,268.0,M*")


def test_every_sentence_checks_out():
    for ln in sentences(FIX, {"depth_ft": 24.6, "water_temp_f": 71.6}):
        body, cs = ln[1:].rstrip("\r\n").split("*")
        assert ln.startswith("$") and ln.endswith("\r\n") and checksum(body) == cs


def test_depth_and_water_temperature():
    s = by_type(sentences(FIX, {"depth_ft": 24.6, "water_temp_f": 71.6}))
    assert s["DPT"].startswith("$SDDPT,7.5,0.0*")
    assert s["DBT"].startswith("$SDDBT,24.6,f,7.5,M,4.1,F*")
    assert s["MTW"].startswith("$YXMTW,22.0,C*")


def test_nothing_made_up_without_a_fix_or_a_sounder():
    s = by_type(sentences(dict(FIX, has_fix=False, fix_quality=0), {"depth_ft": None, "water_temp_f": None}))
    assert s["RMC"].startswith("$GPRMC,140509.00,V,,,,,,,240926,,,N*")
    assert s["GGA"].startswith("$GPGGA,140509.00,,,,,0,00,")
    assert not {"VTG", "HDM", "DPT", "DBT", "MTW"} & set(s)


def test_southern_and_eastern_hemispheres():
    s = by_type(sentences(dict(FIX, lat=-33.5, lon=151.25), {}))
    assert ",3330.0000,S,15115.0000,E," in s["RMC"]


def test_server_sends_each_client_the_sentences():
    async def run():
        server = NmeaServer("127.0.0.1", 0)
        server._server = await asyncio.start_server(server._client, "127.0.0.1", 0)
        port = server._server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        for _ in range(50):              # the server has noticed the client
            if server._writers:
                break
            await asyncio.sleep(0.01)
        await server.publish(sentences(FIX, {"depth_ft": 10.0}))
        got = await asyncio.wait_for(reader.readuntil(b"*"), timeout=2)
        writer.close()
        await server.stop()
        return got
    assert asyncio.run(run()).startswith(b"$GPRMC,140509.00,A,")
