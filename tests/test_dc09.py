from datetime import datetime, timedelta, timezone

import pytest

from alarmhub.protocols import dc09, surgard
from alarmhub.protocols.normalized import from_any_code

KEY = dc09.parse_key("000102030405060708090A0B0C0D0E0F")


def test_crc16_arc():
    assert dc09.crc16(b"123456789") == 0xBB3D


@pytest.mark.parametrize("key", [None, KEY, dc09.parse_key("0123456789abcdef0123456789abcdef0123456789abcdef"),
                                 dc09.parse_key("A" * 32, "ascii")])
def test_roundtrip_sia(key):
    raw = dc09.build_message("SIA-DCS", "1234", "#1234|Nri1/BA501", sequence=42, key=key, extended={"X": "E3.7"})
    frames, rest = dc09.split_stream(raw + b"\n00")
    assert rest == b"\n00"
    fr = dc09.parse_frame(frames[0], key)
    assert fr.encrypted == (key is not None)
    assert fr.sequence == "0042" and fr.account == "1234"
    assert fr.extended == {"X": "E3.7"}
    msg = dc09.to_incoming(fr, "1.2.3.4:1", "tcp")
    ev = msg.events[0]
    assert (ev.code, ev.category, ev.zone, ev.partition) == ("BA", "alarm", "501", "1")
    ack = dc09.parse_frame(dc09.split_stream(dc09.build_ack(fr, key))[0][0], key, accept={"ACK"})
    assert ack.token == "ACK" and ack.sequence == "0042"


def test_adm_cid():
    raw = dc09.build_message("ADM-CID", "5678", "#5678|3401 02 007", key=None)
    msg = dc09.to_incoming(dc09.parse_frame(dc09.split_stream(raw)[0][0]), "", "udp")
    ev = msg.events[0]
    assert ev.code == "R401" and ev.category == "close" and ev.user == "7" and ev.partition == "2"


def test_multi_event_sia_and_modifiers():
    evs = dc09.parse_sia_dcs("Nti10:05/id3/ri2/OP3/ri1/BA004")
    assert [e.code for e in evs] == ["OP", "BA"]
    assert evs[0].user == "3" and evs[0].partition == "2"
    assert evs[1].zone == "4" and evs[1].partition == "1"


def test_bad_crc_and_wrong_key():
    raw = dc09.split_stream(dc09.build_message("SIA-DCS", "1", "#1|NBA1", key=KEY))[0][0]
    with pytest.raises(dc09.DC09Error):
        dc09.parse_frame(b"0000" + raw[4:], KEY)
    with pytest.raises(dc09.DC09Error):
        dc09.parse_frame(raw, dc09.parse_key("11" * 16))
    with pytest.raises(dc09.DC09Error):
        dc09.parse_frame(raw, None)


def test_unknown_token_gives_duh():
    body = '"FOO-BAR"0001R0L0#1234[]'
    with pytest.raises(dc09.DC09Error) as exc:
        dc09.parse_frame(dc09.frame(body).strip())
    assert exc.value.response == "DUH"


def test_timestamp_tolerance():
    old = datetime.now(timezone.utc) - timedelta(minutes=5)
    raw = dc09.build_message("SIA-DCS", "1", "#1|NBA1", key=KEY, timestamp=old)
    fr = dc09.parse_frame(dc09.split_stream(raw)[0][0], KEY)
    assert not dc09.timestamp_ok(fr, 60)
    assert dc09.timestamp_ok(fr, 0)


def test_null_message():
    fr = dc09.parse_frame(dc09.split_stream(dc09.build_message("NULL", "1234", "", key=KEY))[0][0], KEY)
    assert dc09.to_incoming(fr, "", "tcp").is_link_test


def test_surgard():
    msg = surgard.parse(b"5011 181234113001003")
    assert msg.account == "1234" and msg.events[0].code == "E130" and msg.events[0].zone == "3"
    assert surgard.parse(b"1011           @    ").is_link_test


def test_any_code():
    assert from_any_code("BA").category == "alarm"
    assert from_any_code("R130").category == "restore"
    assert from_any_code("1110").category == "fire"
    assert from_any_code("XYZ1", text="algo").description == "algo"
