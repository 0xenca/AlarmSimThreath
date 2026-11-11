"""Convierte una especificación de alarma (dict JSON) en una trama DC-09.

Especificación (todas las claves son opcionales salvo el código)::

    {
      "token": "SIA-DCS" | "ADM-CID" | "NULL",   # por defecto el protocolo del panel
      "code": "BA",              # SIA (2 letras) o Contact ID (3 dígitos)
      "qualifier": "E",          # Contact ID: E nuevo, R restauración, P estado
      "zone": "3", "partition": "1", "user": "",
      "extra_events": [{"code": "BA", "zone": "5", "partition": "1"}],   # SIA: varios en un mensaje
      "time_ti": "" | "auto" | "10:05",          # modificador SIA ti
      "inline_text": "",                         # texto SIA en línea ^...^
      "xdata": {"I": "texto", "V": "https://...", "X": "...", "Y": "..."},
      "auto_xdata": true,        # completar I/R/L con el nombre de zona/sala y datos del panel
      "options": {"encryption": "default"|"on"|"off"|"wrong_key", "timestamp": true,
                  "timestamp_offset": 0, "corrupt_crc": false, "corrupt_length": false,
                  "account_in_data": true}
    }
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..models import Panel, Target
from ..protocol import dc09
from ..protocol.codes import CID, SIA


@dataclass
class Prepared:
    message: dc09.BuiltMessage
    key: bytes | None          # clave para descifrar la respuesta
    token: str
    summary: str


def resolve_key(panel: Panel, target: Target | None) -> bytes | None:
    if panel.enc_key:
        return dc09.parse_key(panel.enc_key, panel.key_format)
    if target and target.enc_key:
        return dc09.parse_key(target.enc_key, target.key_format)
    return None


def _zone_info(panel: Panel, zone: str) -> tuple[str, str]:
    for z in panel.zones:
        if str(z.number) == str(zone).lstrip("0") or (str(zone) in ("0", "") and z.number == 0):
            return z.name, z.room
    return "", ""


def _user_name(panel: Panel, user: str) -> str:
    for u in panel.users:
        if str(u.number) == str(user).lstrip("0"):
            return u.name
    return ""


def describe(token: str, spec: dict[str, Any], panel: Panel | None = None) -> str:
    if token == "NULL":
        return "Prueba de enlace (NULL)"
    code = str(spec.get("code", "")).upper()
    if token == "ADM-CID":
        desc = CID.get(code, (f"CID {code}",))[0]
        q = {"E": "nuevo", "R": "restauración", "P": "estado", "1": "nuevo", "3": "restauración", "6": "estado"}
        txt = f"{spec.get('qualifier', 'E')}{code} {desc} ({q.get(str(spec.get('qualifier', 'E')).upper(), '')})"
    else:
        txt = f"{code} {SIA.get(code, (f'SIA {code}',))[0]}"
        if spec.get("extra_events"):
            txt += f" +{len(spec['extra_events'])} eventos"
    if spec.get("zone") not in (None, ""):
        name = _zone_info(panel, spec["zone"])[0] if panel else ""
        txt += f" · zona {spec['zone']}" + (f" ({name})" if name else "")
    if spec.get("partition") not in (None, ""):
        txt += f" · part. {spec['partition']}"
    if spec.get("user") not in (None, ""):
        txt += f" · usuario {spec['user']}"
    return txt[:255]


def prepare(panel: Panel, target: Target | None, spec: dict[str, Any], sequence: int) -> Prepared:
    token = (spec.get("token") or panel.protocol or "SIA-DCS").upper()
    opts = spec.get("options") or {}

    # --- cifrado
    key = resolve_key(panel, target)
    mode = opts.get("encryption", "default")
    if mode == "default":
        mode = panel.encryption if panel.encryption in ("on", "off") else ("on" if key else "off")
    send_key: bytes | None = None
    if mode == "on":
        if key is None:
            raise dc09.DC09Error("Cifrado activado pero el panel ni el destino tienen clave AES")
        send_key = key
    elif mode == "wrong_key":
        send_key = secrets.token_bytes(len(key) if key else 16)

    # --- datos
    zone = str(spec.get("zone") or "").strip()
    partition = str(spec.get("partition") or "").strip()
    user = str(spec.get("user") or "").strip()
    if token == "SIA-DCS":
        code = str(spec.get("code") or "").upper()
        addr = zone
        if SIA.get(code, ("", "", "zone"))[2] == "user" and user and not zone:
            addr = user
        events = [dc09.SIAEvent(code, addr, partition, user if addr != user else "")]
        for ex in spec.get("extra_events") or []:
            events.append(dc09.SIAEvent(str(ex.get("code", "")).upper(), str(ex.get("zone") or ""),
                                        str(ex.get("partition") or ""), str(ex.get("user") or "")))
        ti = spec.get("time_ti") or ""
        if ti == "auto":
            ti = datetime.now().strftime("%H:%M")
        data = dc09.sia_dcs_data(events, time_ti=ti, inline_text=spec.get("inline_text") or "")
    elif token == "ADM-CID":
        address = zone if zone else (user or "0")
        data = dc09.adm_cid_data(spec.get("qualifier") or "E", spec.get("code") or "", partition or "0", address)
    else:
        data = ""

    # --- xdata
    xdata: dict[str, str] = {}
    if token != "NULL":
        if spec.get("auto_xdata", True):
            xdata.update({k: v for k, v in (panel.xdata_defaults or {}).items() if v})
            zname, room = _zone_info(panel, zone) if zone else ("", "")
            if zname and "I" not in xdata:
                xdata["I"] = zname
            if room:
                xdata["R"] = room
            if user and not zname:
                uname = _user_name(panel, user)
                if uname:
                    xdata["I"] = uname
        xdata.update({k.upper(): v for k, v in (spec.get("xdata") or {}).items() if v not in (None, "")})

    rcvr = panel.receiver or (target.receiver if target else "0")
    if rcvr == "-":
        rcvr = ""
    line = panel.line or (target.line if target else "0") or "0"
    built = dc09.build_message(token, panel.account, data, xdata, dc09.BuildOptions(
        sequence=sequence, receiver=rcvr, line=line, key=send_key,
        timestamp=bool(opts.get("timestamp", target.include_timestamp if target else True)),
        timestamp_offset=int(opts.get("timestamp_offset") or 0),
        account_in_data=bool(opts.get("account_in_data", True)),
        corrupt_crc=bool(opts.get("corrupt_crc")), corrupt_length=bool(opts.get("corrupt_length"))))
    return Prepared(message=built, key=key, token=token,
                    summary=describe(token, spec, panel))
