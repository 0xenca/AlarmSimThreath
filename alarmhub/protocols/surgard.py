"""Formato Sur-Gard MLR2/System III (Contact ID sobre TCP), usado por
receptoras y software de CRA:  ``5RRL 18AAAAQEEEGGZZZ<DC4>``.

* ``1011           @    <DC4>`` es el latido (heartbeat) de la receptora.
* Cada mensaje se confirma con ACK (0x06).
"""
from __future__ import annotations

import re

from .normalized import IncomingMessage, from_cid

TERMINATOR = b"\x14"
ACK = b"\x06"
NAK = b"\x15"
MSG_RE = re.compile(r"^5(?P<rr>\d{2})(?P<line>\d{1,3})\s*18\s*(?P<acct>[0-9A-F]{4,6})\s*(?P<q>[136])"
                    r"(?P<code>[0-9A-F]{3})(?P<part>[0-9A-F]{2})(?P<zone>[0-9A-F]{3})")


def parse(raw: bytes, remote: str = "") -> IncomingMessage | None:
    text = raw.decode("latin-1").strip("\x14\r\n ")
    if text.startswith("1"):
        return IncomingMessage(protocol="SURGARD", account="", raw=text, remote=remote, transport="tcp",
                               is_link_test=True)
    m = MSG_RE.match(text)
    if not m:
        return None
    ev = from_cid(m["q"], m["code"], partition=m["part"].lstrip("0") or "0", zone=m["zone"].lstrip("0") or "0")
    return IncomingMessage(protocol="SURGARD", account=m["acct"], receiver=m["rr"], line_prefix=m["line"],
                           raw=text, remote=remote, transport="tcp", events=[ev])
