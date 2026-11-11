"""Implementación de ANSI/SIA DC-09 (2013 / 2021).

Trama:  <LF><CRC><0LLL><"ID"><seq><Rrcvr><Lpref><#acct>[datos][ext...]<_timestamp><CR>

* CRC-16 (polinomio 0x8005 reflejado / "ARC") sobre el bloque desde la
  primera comilla hasta el final del mensaje (sin <CR>).
* LLL: longitud en hexadecimal del mismo bloque.
* Encriptación opcional AES-128/192/256 en modo CBC con IV a ceros. Si el ID
  empieza por '*' todo lo que va tras el número de abonado se transmite como
  texto hexadecimal cifrado tras el '['. El texto en claro empieza con caracteres de
  relleno, seguidos de '|', seguidos de los datos (sin el '[' inicial).

Soporta los tokens SIA-DCS, ADM-CID y NULL (prueba de enlace).
"""
from __future__ import annotations

import re
import secrets
import string
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .normalized import IncomingMessage, NormalizedEvent, from_cid, from_sia

LF = "\n"
CR = "\r"

FRAME_RE = re.compile(
    r'^(?P<crc>[0-9A-Fa-f]{4})(?P<len>[0-9A-Fa-f]{4})"(?P<id>\*?[A-Z0-9\-]+)"'
    r'(?P<seq>\d{4})(?:R(?P<rcvr>[0-9A-Fa-f]{1,6}))?L(?P<line>[0-9A-Fa-f]{1,6})'
    r'(?:#(?P<acct>[0-9A-Fa-f]{1,16}))?(?P<rest>.*)$',
    re.S,
)
TIMESTAMP_RE = re.compile(r"_(\d{2}):(\d{2}):(\d{2}),(\d{2})-(\d{2})-(\d{4})$")
SUPPORTED_IDS = {"SIA-DCS", "ADM-CID", "NULL"}
PAD_CHARS = string.ascii_letters + string.digits


class DC09Error(Exception):
    """Error de protocolo. ``response`` indica la respuesta a enviar (NAK / DUH)."""

    def __init__(self, message: str, response: str = "NAK", frame: "DC09Frame | None" = None):
        super().__init__(message)
        self.response = response
        self.frame = frame


# ---------------------------------------------------------------- utilidades
def crc16(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def parse_key(key: str | None, key_format: str = "hex") -> bytes | None:
    if not key:
        return None
    key = key.strip()
    if key_format == "ascii":
        raw = key.encode("latin-1")
    else:
        try:
            raw = bytes.fromhex(key)
        except ValueError as exc:
            raise ValueError("La clave AES no es hexadecimal válida") from exc
    if len(raw) not in (16, 24, 32):
        raise ValueError("La clave AES debe tener 128, 192 o 256 bits")
    return raw


def aes_encrypt(key: bytes, plaintext: str) -> str:
    enc = Cipher(algorithms.AES(key), modes.CBC(bytes(16))).encryptor()
    return (enc.update(plaintext.encode("latin-1")) + enc.finalize()).hex().upper()


def aes_decrypt(key: bytes, hextext: str) -> str:
    data = bytes.fromhex(hextext)
    if len(data) % 16:
        raise ValueError("Bloque cifrado con longitud no múltiplo de 16")
    dec = Cipher(algorithms.AES(key), modes.CBC(bytes(16))).decryptor()
    return (dec.update(data) + dec.finalize()).decode("latin-1")


def format_timestamp(dt: datetime | None = None) -> str:
    dt = (dt or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return dt.strftime("_%H:%M:%S,%m-%d-%Y")


def parse_timestamp(ts: str) -> datetime | None:
    m = TIMESTAMP_RE.search(ts)
    if not m:
        return None
    hh, mi, ss, mm, dd, yyyy = map(int, m.groups())
    try:
        return datetime(yyyy, mm, dd, hh, mi, ss, tzinfo=timezone.utc)
    except ValueError:
        return None


def frame(body: str) -> bytes:
    raw = body.encode("latin-1")
    return f"{LF}{crc16(raw):04X}0{len(raw):03X}{body}{CR}".encode("latin-1")


def _encrypt_block(key: bytes, content_without_bracket: str) -> str:
    padlen = (-(len(content_without_bracket) + 1)) % 16
    pad = "".join(secrets.choice(PAD_CHARS) for _ in range(padlen))
    return aes_encrypt(key, f"{pad}|{content_without_bracket}")


# ---------------------------------------------------------------- tramas
@dataclass
class DC09Frame:
    message_id: str
    sequence: str
    receiver: str | None
    line: str
    account: str | None
    data: str                       # contenido de [ ... ] sin corchetes
    extended: dict[str, str] = field(default_factory=dict)
    timestamp: datetime | None = None
    encrypted: bool = False
    raw: str = ""

    @property
    def token(self) -> str:
        return self.message_id.lstrip("*")


def split_stream(buffer: bytes) -> tuple[list[bytes], bytes]:
    """Separa un buffer TCP en tramas completas <LF>...<CR>. Devuelve (tramas, resto)."""
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


def peek_account(raw: bytes) -> tuple[str | None, str, str | None]:
    """Extrae (abonado, prefijo de línea, receptor) sin validar, para buscar la clave."""
    m = FRAME_RE.match(raw.decode("latin-1").strip("\n\r"))
    if not m:
        return None, "", None
    return m.group("acct"), m.group("line") or "", m.group("rcvr")


def parse_frame(raw: bytes, key: bytes | None = None, check_crc: bool = True,
                accept: set[str] | frozenset[str] = frozenset(SUPPORTED_IDS)) -> DC09Frame:
    text = raw.decode("latin-1").strip("\n\r")
    m = FRAME_RE.match(text)
    if not m:
        raise DC09Error("Trama DC-09 mal formada")
    body = text[8:]
    length = int(m.group("len")[1:], 16)
    if check_crc:
        if int(m.group("crc"), 16) != crc16(body.encode("latin-1")):
            raise DC09Error("CRC incorrecto")
        if length != len(body.encode("latin-1")):
            raise DC09Error("Longitud incorrecta")

    fr = DC09Frame(message_id=m.group("id"), sequence=m.group("seq"), receiver=m.group("rcvr"),
                   line=m.group("line"), account=m.group("acct"), data="", raw=text,
                   encrypted=m.group("id").startswith("*"))
    rest = m.group("rest")
    if fr.token not in accept:
        raise DC09Error(f"Token no soportado: {fr.message_id}", response="DUH", frame=fr)

    if fr.encrypted:
        if key is None:
            raise DC09Error("Mensaje cifrado sin clave configurada para el abonado", frame=fr)
        try:
            plain = aes_decrypt(key, rest.strip().lstrip("["))
        except ValueError as exc:
            raise DC09Error(f"No se puede descifrar: {exc}", frame=fr) from exc
        if "|" not in plain or "]" not in plain or not all(32 <= ord(c) < 127 for c in plain):
            raise DC09Error("Clave AES incorrecta (contenido descifrado inválido)", frame=fr)
        rest = "[" + plain.split("|", 1)[1]

    if not rest.startswith("["):
        raise DC09Error("Falta el bloque de datos", frame=fr)
    end = rest.find("]")
    if end < 0:
        raise DC09Error("Bloque de datos sin cerrar", frame=fr)
    fr.data = rest[1:end]
    rest = rest[end + 1:]
    # bloques extendidos [Xxxx][Yyyy][Hxxx]...
    while rest.startswith("["):
        end = rest.find("]")
        if end < 0:
            break
        block = rest[1:end]
        if block:
            fr.extended[block[0]] = block[1:]
        rest = rest[end + 1:]
    if rest.startswith("_"):
        fr.timestamp = parse_timestamp(rest)
    return fr


def build_message(token: str, account: str, data: str, sequence: int = 1, receiver: str | None = "0",
                  line: str = "0", key: bytes | None = None, timestamp: bool | datetime = True,
                  extended: dict[str, str] | None = None) -> bytes:
    """Construye una trama DC-09 (usado por el simulador y los tests)."""
    ext = "".join(f"[{k}{v}]" for k, v in (extended or {}).items())
    if isinstance(timestamp, datetime):
        ts = format_timestamp(timestamp)
    elif timestamp or key:
        ts = format_timestamp()
    else:
        ts = ""
    ident = f'"{"*" if key else ""}{token}"'
    head = f"{ident}{sequence:04d}" + (f"R{receiver}" if receiver is not None else "") + f"L{line}#{account}"
    if key:
        body = head + "[" + _encrypt_block(key, f"{data}]{ext}{ts}")
    else:
        body = head + f"[{data}]{ext}{ts}"
    return frame(body)


def build_ack(fr: DC09Frame, key: bytes | None = None) -> bytes:
    head = f'"{"*" if fr.encrypted else ""}ACK"{fr.sequence}' + (f"R{fr.receiver}" if fr.receiver else "") \
        + f"L{fr.line}#{fr.account or ''}"
    if fr.encrypted and key:
        return frame(head + "[" + _encrypt_block(key, "]" + format_timestamp()))
    return frame(head + "[]" + (format_timestamp() if fr.timestamp else ""))


def build_nak() -> bytes:
    return frame(f'"NAK"0000R0L0A0[]{format_timestamp()}')


def build_duh(fr: DC09Frame | None) -> bytes:
    if fr is None:
        return frame('"DUH"0000R0L0[]')
    return frame(f'"DUH"{fr.sequence}' + (f"R{fr.receiver}" if fr.receiver else "") + f"L{fr.line}[]")


def timestamp_ok(fr: DC09Frame, tolerance_s: int) -> bool:
    if tolerance_s <= 0:
        return True
    if fr.timestamp is None:
        return not fr.encrypted  # el estándar exige timestamp en mensajes cifrados
    delta = fr.timestamp - datetime.now(timezone.utc)
    return timedelta(seconds=-tolerance_s) <= delta <= timedelta(seconds=tolerance_s)


# ---------------------------------------------------------------- contenido
CID_RE = re.compile(r"([136])\s?([0-9A-F]{3})\s?([0-9A-F]{2})\s?([0-9A-F]{3})")


def parse_sia_dcs(data: str) -> list[NormalizedEvent]:
    """Interpreta el bloque SIA DC-04, p. ej. 'Nri1/BA501' o 'Nti10:05/id3/ri2/OP3'."""
    msg = data
    text = None
    if "^" in msg:  # texto ASCII opcional entre ^...^
        msg, _, tail = msg.partition("^")
        text = tail.rstrip("^").strip() or None
    if msg[:1] in ("N", "O"):
        msg = msg[1:]
    events: list[NormalizedEvent] = []
    mods: dict[str, str] = {}
    i = 0
    n = len(msg)
    while i < n:
        ch = msg[i]
        if ch == "/":
            i += 1
            continue
        two = msg[i:i + 2]
        if len(two) == 2 and two.islower() and two.isalpha():
            j = i + 2
            while j < n and not msg[j].isalpha() and msg[j] != "/":
                j += 1
            mods[two] = msg[i + 2:j]
            i = j
            continue
        if len(two) == 2 and two.isupper() and two.isalpha():
            j = i + 2
            while j < n and not msg[j].isalpha() and msg[j] != "/":
                j += 1
            addr = msg[i + 2:j] or None
            if addr and addr.isdigit():
                addr = addr.lstrip("0") or "0"
            ev = from_sia(two, zone=addr, partition=mods.get("ri") or mods.get("pi"), text=text)
            if mods.get("id"):
                ev.user = mods["id"]
            if two[0] in ("O", "C") and addr and not mods.get("id"):
                ev.user = addr  # en aperturas/cierres la dirección es el usuario
            ev.extra = {k: v for k, v in mods.items() if v}
            events.append(ev)
            i = j
            continue
        i += 1
    return events


def parse_adm_cid(data: str) -> list[NormalizedEvent]:
    m = CID_RE.search(data)
    if not m:
        return []
    q, code, part, zone = m.groups()
    return [from_cid(q, code, partition=part.lstrip("0") or "0", zone=zone.lstrip("0") or "0")]


def to_incoming(fr: DC09Frame, remote: str, transport: str) -> IncomingMessage:
    data = fr.data
    account = fr.account or ""
    if data.startswith("#"):
        # datos con abonado propio: '#1234|Nri1/BA501'
        head, _, data = data.partition("|")
        account = account or head[1:]
    msg = IncomingMessage(protocol=fr.token, account=account, line_prefix=fr.line or "", receiver=fr.receiver or "",
                          sequence=fr.sequence, raw=fr.raw, remote=remote, transport=transport,
                          encrypted=fr.encrypted, extended=fr.extended,
                          panel_timestamp=fr.timestamp.isoformat() if fr.timestamp else None)
    if fr.token == "NULL":
        msg.is_link_test = True
    elif fr.token == "SIA-DCS":
        msg.events = parse_sia_dcs(data)
    elif fr.token == "ADM-CID":
        msg.events = parse_adm_cid(data)
    for ev in msg.events:
        if fr.extended:
            ev.extra.update({f"ext_{k}": v for k, v in fr.extended.items()})
    return msg
