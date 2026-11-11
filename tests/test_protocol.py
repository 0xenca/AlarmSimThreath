from datetime import datetime, timedelta, timezone

import pytest

from alarmsim.protocol import dc09

K128 = dc09.parse_key("000102030405060708090A0B0C0D0E0F")
K256 = dc09.parse_key("00" * 32)


def roundtrip(m, key):
    return dc09.parse(dc09.split_stream(m.frame)[0][0], key)


def test_crc_arc():
    assert dc09.crc16(b"123456789") == 0xBB3D


@pytest.mark.parametrize("key", [None, K128, dc09.parse_key("11" * 24), K256, dc09.parse_key("B" * 16, "ascii")])
def test_sia_with_xdata(key):
    data = dc09.sia_dcs_data([dc09.SIAEvent("BA", "3", "1"), dc09.SIAEvent("BA", "5", "2", "7")], time_ti="10:05",
                             inline_text="hola")
    assert data == "Nti10:05/ri1/BA3/id7/ri2/BA5^hola^"
    x = {"I": "Puerta [principal] €", "V": "https://v.example/clip?id=1&a=b", "X": "E3.7", "Y": "N40.4", "Q": ""}
    m = dc09.build_message("SIA-DCS", "12ab", data, x, dc09.BuildOptions(sequence=42, receiver="1A", line="2", key=key))
    assert m.encrypted == (key is not None)
    p = roundtrip(m, key)
    assert p.crc_ok and p.length_ok
    assert (p.token, p.sequence, p.receiver, p.line, p.account) == ("SIA-DCS", "0042", "1A", "2", "12AB")
    assert p.data == "#12AB|" + data
    assert p.xdata == {"I": "Puerta (principal) €", "V": "https://v.example/clip?id=1&a=b", "X": "E3.7", "Y": "N40.4"}
    assert p.timestamp is not None
    ack = dc09.parse(dc09.split_stream(dc09.build_response("ACK", p, key))[0][0], key)
    assert ack.token == "ACK" and ack.sequence == "0042" and ack.encrypted == (key is not None)


def test_encrypted_block_is_padded():
    m = dc09.build_message("SIA-DCS", "1234", "NBA1", None, dc09.BuildOptions(key=K128))
    hexpart = m.body.split("[", 1)[1]
    assert len(bytes.fromhex(hexpart)) % 16 == 0


def test_adm_cid():
    assert dc09.adm_cid_data("R", "401", 2, 7) == "3401 02 007"
    with pytest.raises(dc09.DC09Error):
        dc09.adm_cid_data("X", "130", 1, 1)
    with pytest.raises(dc09.DC09Error):
        dc09.adm_cid_data("E", "130", 1, 1000)


def test_receiver_omitted_and_null():
    m = dc09.build_message("NULL", "1234", "", None, dc09.BuildOptions(receiver="", key=K128))
    assert '"*NULL"0001L0#1234[' in m.body
    p = roundtrip(m, K128)
    assert p.token == "NULL" and p.receiver is None and p.data == ""


def test_faults_and_offsets():
    m = dc09.build_message("SIA-DCS", "1234", "NBA1", None, dc09.BuildOptions(corrupt_crc=True, corrupt_length=True))
    p = roundtrip(m, None)
    assert not p.crc_ok and not p.length_ok
    m = dc09.build_message("SIA-DCS", "1234", "NBA1", None, dc09.BuildOptions(timestamp_offset=-600))
    assert roundtrip(m, None).timestamp < datetime.now(timezone.utc) - timedelta(seconds=500)
    m = dc09.build_message("SIA-DCS", "1234", "NBA1", None, dc09.BuildOptions(timestamp=False))
    assert roundtrip(m, None).timestamp is None


def test_wrong_key_detected():
    m = dc09.build_message("SIA-DCS", "1234", "NBA1", {"I": "x"}, dc09.BuildOptions(key=K128))
    with pytest.raises(dc09.DC09Error):
        roundtrip(m, dc09.parse_key("FF" * 16))


def test_validation():
    with pytest.raises(dc09.DC09Error):
        dc09.build_message("SIA-DCS", "12", "NBA1")          # abonado demasiado corto
    with pytest.raises(dc09.DC09Error):
        dc09.sia_dcs_data([dc09.SIAEvent("B1")])
    with pytest.raises(ValueError):
        dc09.parse_key("0011")
    assert dc09.build_message("SIA-DCS", "1234", "NBA1", None, dc09.BuildOptions(sequence=10000)).sequence == "0001"
