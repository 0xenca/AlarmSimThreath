"""Construcción y análisis de tramas ANSI/SIA DC-09.

Trama:  <LF><CRC><0LLL>"<ID>"<seq>[R<rcvr>]L<pref>#<acct>[<datos>][<xdata>...]_<timestamp><CR>

* CRC-16/ARC sobre el bloque desde la primera comilla hasta el final (sin <CR>).
* 0LLL: longitud en hexadecimal de ese mismo bloque.
* Cifrado opcional AES-128/192/256 CBC con IV a ceros: el ID lleva '*', el '['
  inicial queda en claro y el resto (datos, xdata y timestamp) se envía como hex.
  El texto en claro es ``<relleno>|<datos sin '['>]...`` con longitud múltiplo de 16.
"""
from __future__ import annotations

import re
import secrets
import string
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .xdata import normalize as normalize_xdata

ENC = "cp1252"
PAD_CHARS = string.ascii_letters + string.digits
TOKENS = ("SIA-DCS", "ADM-CID", "NULL")
FRAME_RE = re.compile(
    r'^(?P<crc>[0-9A-Fa-f]{4})(?P<len>[0-9A-Fa-f]{4})"(?P<id>\*?[A-Z0-9\-]+)"'
    r'(?P<seq>\d{4})(?:R(?P<rcvr>[0-9A-Fa-f]{1,6}))?(?:L(?P<line>[0-9A-Fa-f]{1,6}))?'
    r'(?:#(?P<acct>[0-9A-Fa-f]{1,16}))?(?P<rest>.*)$', re.S)
TS_RE = re.compile(r"_(\d{2}):(\d{2}):(\d{2}),(\d{2})-(\d{2})-(\d{4})")


class DC09Error(Exception):
    pass


# ---------------------------------------------------------------- utilidades
def crc16(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def parse_key(key: str | None, key_format: str = "hex") -> bytes | None:
    if not key or not key.strip():
        return None
    key = key.strip()
    if key_format == "ascii":
        raw = key.encode("latin-1")
    else:
        try:
            raw = bytes.fromhex(key.replace(" ", ""))
        except ValueError as exc:
            raise ValueError("La clave AES no es hexadecimal válida") from exc
    if len(raw) not in (16, 24, 32):
        raise ValueError(f"La clave AES debe tener 128, 192 o 256 bits (tiene {len(raw) * 8})")
    return raw


def aes_encrypt(key: bytes, plaintext: str) -> str:
    enc = Cipher(algorithms.AES(key), modes.CBC(bytes(16))).encryptor()
    return (enc.update(plaintext.encode(ENC, "replace")) + enc.finalize()).hex().upper()


def aes_decrypt(key: bytes, hextext: str) -> str:
    data = bytes.fromhex(hextext)
    if not data or len(data) % 16:
        raise ValueError("bloque cifrado con longitud no múltiplo de 16")
    dec = Cipher(algorithms.AES(key), modes.CBC(bytes(16))).decryptor()
    return (dec.update(data) + dec.finalize()).decode(ENC, "replace")


def format_timestamp(dt: datetime | None = None) -> str:
    dt = (dt or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return dt.strftime("_%H:%M:%S,%m-%d-%Y")


def parse_timestamp(text: str) -> datetime | None:
    m = TS_RE.search(text)
    if not m:
        return None
    hh, mi, ss, mm, dd, yyyy = map(int, m.groups())
    try:
        return datetime(yyyy, mm, dd, hh, mi, ss, tzinfo=timezone.utc)
    except ValueError:
        return None


def frame(body: str, corrupt_crc: bool = False, corrupt_length: bool = False) -> bytes:
    raw = body.encode(ENC, "replace")
    crc = crc16(raw) ^ (0x1234 if corrupt_crc else 0)
    length = len(raw) + (1 if corrupt_length else 0)
    return b"\n" + f"{crc:04X}0{length:03X}".encode() + raw + b"\r"


def encrypt_block(key: bytes, content_without_bracket: str) -> str:
    plain_len = len(content_without_bracket.encode(ENC, "replace")) + 1
    pad = "".join(secrets.choice(PAD_CHARS) for _ in range((-plain_len) % 16))
    return aes_encrypt(key, f"{pad}|{content_without_bracket}")


# ---------------------------------------------------------------- contenido de eventos
@dataclass
class SIAEvent:
    code: str                       # 2 letras: BA, FA, OP...
    address: str = ""               # zona / usuario / dato asociado al código
    partition: str = ""             # modificador ri
    user: str = ""                  # modificador id
    text: str = ""                  # texto ASCII en línea (^...^)


def sia_dcs_data(events: list[SIAEvent], time_ti: str = "", inline_text: str = "") -> str:
    """Bloque SIA DC-04, p. ej. ``Nti10:05/id3/ri1/BA501``."""
    if not events:
        raise DC09Error("Se necesita al menos un evento")
    parts: list[str] = []
    if time_ti:
        parts.append(f"ti{time_ti}")
    last_ri = last_id = None
    for ev in events:
        code = ev.code.strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", code):
            raise DC09Error(f"Código SIA inválido: {ev.code!r}")
        if ev.user and ev.user != last_id:
            parts.append(f"id{ev.user}")
            last_id = ev.user
        if ev.partition and ev.partition != last_ri:
            parts.append(f"ri{ev.partition}")
            last_ri = ev.partition
        parts.append(f"{code}{ev.address}")
    data = "N" + "/".join(parts)
    text = inline_text.replace("^", "").replace("[", "(").replace("]", ")")
    if text:
        data += f"^{text}^"
    return data


def adm_cid_data(qualifier: str, code: str, partition: str | int, zone: str | int) -> str:
    """Bloque Contact ID, p. ej. ``1130 01 015`` (Q EEE GG CCC)."""
    q = {"E": "1", "R": "3", "P": "6"}.get(str(qualifier).upper(), str(qualifier))
    if q not in ("1", "3", "6"):
        raise DC09Error("Calificador Contact ID inválido (E/1, R/3, P/6)")
    code = str(code).strip().upper()
    if not re.fullmatch(r"[0-9A-F]{3}", code):
        raise DC09Error(f"Código Contact ID inválido: {code!r}")

    def num(v: str | int, width: int) -> str:
        s = str(v or 0).strip().upper()
        if not re.fullmatch(r"[0-9A-F]+", s) or len(s) > width:
            raise DC09Error(f"Valor fuera de rango para {width} dígitos: {v!r}")
        return s.zfill(width)

    return f"{q}{code} {num(partition, 2)} {num(zone, 3)}"


# ---------------------------------------------------------------- mensaje completo
@dataclass
class BuildOptions:
    sequence: int = 1
    receiver: str | None = "0"        # None o "" = omitir el campo R
    line: str = "0"
    key: bytes | None = None
    timestamp: bool = True            # obligatorio si se cifra
    timestamp_offset: int = 0         # segundos (para probar la tolerancia del receptor)
    account_in_data: bool = True      # repetir '#acct|' dentro del bloque de datos
    corrupt_crc: bool = False
    corrupt_length: bool = False


@dataclass
class BuiltMessage:
    frame: bytes
    body: str                         # trama sin LF/CRC/LLL/CR
    plaintext: str                    # contenido en claro (antes de cifrar) para mostrar
    encrypted: bool
    sequence: str

    @property
    def printable(self) -> str:
        return self.frame.decode(ENC, "replace").replace("\n", "<LF>").replace("\r", "<CR>")


def build_message(token: str, account: str, data: str, xdata: dict[str, str] | None = None,
                  opts: BuildOptions | None = None) -> BuiltMessage:
    o = opts or BuildOptions()
    token = token.upper()
    if token not in TOKENS:
        raise DC09Error(f"Token no soportado: {token}")
    account = account.strip().upper()
    if not re.fullmatch(r"[0-9A-F]{3,16}", account):
        raise DC09Error("El abonado debe tener de 3 a 16 caracteres hexadecimales")
    line = (o.line or "0").strip().upper()
    if not re.fullmatch(r"[0-9A-F]{1,6}", line):
        raise DC09Error("Prefijo de línea L inválido (1-6 hex)")
    rcvr = (o.receiver or "").strip().upper()
    if rcvr and not re.fullmatch(r"[0-9A-F]{1,6}", rcvr):
        raise DC09Error("Número de receptor R inválido (1-6 hex)")
    seq = f"{(o.sequence - 1) % 9999 + 1:04d}"

    payload = "" if token == "NULL" else (f"#{account}|{data}" if o.account_in_data else data)
    ext = "".join(f"[{k}{v}]" for k, v in normalize_xdata(xdata))
    ts = ""
    if o.timestamp or o.key:
        ts = format_timestamp(datetime.now(timezone.utc) + timedelta(seconds=o.timestamp_offset))
    head = f'"{"*" if o.key else ""}{token}"{seq}' + (f"R{rcvr}" if rcvr else "") + f"L{line}#{account}"
    plain_tail = f"[{payload}]{ext}{ts}"
    if o.key:
        body = head + "[" + encrypt_block(o.key, f"{payload}]{ext}{ts}")
    else:
        body = head + plain_tail
    return BuiltMessage(frame=frame(body, o.corrupt_crc, o.corrupt_length), body=body,
                        plaintext=head.replace('"*', '"') + plain_tail, encrypted=bool(o.key), sequence=seq)


# ---------------------------------------------------------------- análisis (respuestas y receptor)
@dataclass
class Parsed:
    token: str
    encrypted: bool
    sequence: str
    receiver: str | None
    line: str | None
    account: str | None
    data: str = ""
    xdata: dict[str, str] = field(default_factory=dict)
    timestamp: datetime | None = None
    crc_ok: bool = True
    length_ok: bool = True
    raw: str = ""
    decrypted: str = ""


def split_stream(buffer: bytes) -> tuple[list[bytes], bytes]:
    frames = []
    while True:
        start = buffer.find(b"\n")
        if start < 0:
            return frames, b""
        end = buffer.find(b"\r", start)
        if end < 0:
            return frames, buffer[start:]
        frames.append(buffer[start + 1:end])
        buffer = buffer[end + 1:]


def peek_account(raw: bytes) -> str | None:
    m = FRAME_RE.match(raw.decode(ENC, "replace").strip("\r\n"))
    return m.group("acct") if m else None


def parse(raw: bytes, key: bytes | None = None) -> Parsed:
    text = raw.decode(ENC, "replace").strip("\r\n")
    m = FRAME_RE.match(text)
    if not m:
        raise DC09Error("Trama DC-09 mal formada")
    body = text[8:]
    p = Parsed(token=m.group("id").lstrip("*"), encrypted=m.group("id").startswith("*"), sequence=m.group("seq"),
               receiver=m.group("rcvr"), line=m.group("line"), account=m.group("acct"), raw=text)
    p.crc_ok = int(m.group("crc"), 16) == crc16(body.encode(ENC, "replace"))
    p.length_ok = int(m.group("len")[1:], 16) == len(body.encode(ENC, "replace"))
    rest = m.group("rest")
    if p.encrypted:
        if key is None:
            raise DC09Error("Mensaje cifrado y no hay clave para descifrarlo")
        try:
            plain = aes_decrypt(key, rest.strip().lstrip("["))
        except ValueError as exc:
            raise DC09Error(f"No se puede descifrar: {exc}") from exc
        if "|" not in plain or "]" not in plain or "\ufffd" in plain or any(ord(c) < 32 for c in plain):
            raise DC09Error("Clave AES incorrecta (contenido descifrado inválido)")
        rest = "[" + plain.split("|", 1)[1]
        p.decrypted = rest
    if rest.startswith("["):
        end = rest.find("]")
        if end >= 0:
            p.data = rest[1:end]
            rest = rest[end + 1:]
        while rest.startswith("["):
            end = rest.find("]")
            if end < 0:
                break
            block = rest[1:end]
            if block:
                p.xdata[block[0]] = block[1:]
            rest = rest[end + 1:]
    p.timestamp = parse_timestamp(rest)
    return p


def build_response(kind: str, req: Parsed | None, key: bytes | None = None) -> bytes:
    """Respuesta de receptor (ACK / NAK / DUH), usada por el receptor de pruebas."""
    if kind == "NAK" or req is None:
        return frame(f'"NAK"0000R0L0A0[]{format_timestamp()}')
    head = f'"{"*" if (req.encrypted and key) else ""}{kind}"{req.sequence}' + \
        (f"R{req.receiver}" if req.receiver else "") + f"L{req.line or '0'}" + \
        (f"#{req.account}" if kind == "ACK" and req.account else "")
    if req.encrypted and key:
        return frame(head + "[" + encrypt_block(key, "]" + format_timestamp()))
    return frame(head + "[]" + (format_timestamp() if req.timestamp else ""))
