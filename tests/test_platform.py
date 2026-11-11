"""Pruebas de integración: receptor DC-09 real (TCP) + procesador + workflows + web."""
import asyncio
import re

import pytest
from fastapi.testclient import TestClient

from alarmhub.bootstrap import init_db
from alarmhub.db import session_scope
from alarmhub.engine.context import matches
from alarmhub.models import Alarm, Client, Event, Panel, Workflow, WorkflowRun, Zone
from alarmhub.protocols import dc09

KEY_HEX = "000102030405060708090A0B0C0D0E0F"
KEY = dc09.parse_key(KEY_HEX)


@pytest.fixture(scope="module")
def setup_db():
    init_db("Admin12345")
    with session_scope() as db:
        c = Client(code="T1", name="Cliente Test", group="norte")
        db.add(c)
        db.flush()
        p = Panel(client_id=c.id, account="1234", enc_key=KEY_HEX, require_encryption=True)
        db.add(p)
        db.flush()
        db.add(Zone(panel_id=p.id, number="3", name="Puerta"))
        db.add(Panel(client_id=c.id, account="2222"))
        db.add(Panel(client_id=c.id, account="3333"))
        db.query(Workflow).update({Workflow.enabled: False})
        db.add(Workflow(name="buffer", order=1, enabled=True, definition={
            "trigger": {"accounts": ["1234"], "categories": ["alarm"]}, "hold_alarm": True,
            "steps": [{"type": "wait_for", "categories": ["alarm"], "different_zone": True, "timeout": 0.5,
                       "on_match": [{"type": "release_alarm"}, {"type": "set_priority", "value": 1}],
                       "on_timeout": [{"type": "close_alarm", "comment": "no confirmada"}]}]}))
        db.add(Workflow(name="count", order=2, enabled=True, definition={
            "trigger": {"accounts": ["2222"]},
            "steps": [{"type": "count", "codes": ["BA"], "window": 60, "threshold": 2,
                       "then": [{"type": "tag", "value": "rafaga"}]},
                      {"type": "comment", "text": "zona {zone}"}]}))
        db.add(Workflow(name="failsafe", order=3, enabled=True, definition={
            "trigger": {"accounts": ["3333"]}, "hold_alarm": True, "steps": [{"type": "delay", "seconds": 0.1}]}))
    yield


async def _send(port, frames):
    from alarmhub.receivers import dc09_server
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    out = []
    for f in frames:
        writer.write(f)
        await writer.drain()
        out.append(await reader.readuntil(b"\r"))
    writer.close()
    return out


def test_end_to_end(setup_db):
    from alarmhub.engine.processor import processor
    from alarmhub.receivers import dc09_server

    async def scenario():
        await processor.start()
        import socket
        with socket.socket() as sk:
            sk.bind(("127.0.0.1", 0))
            port = sk.getsockname()[1]
        servers = await dc09_server.start("127.0.0.1", port, 0)
        try:
            # 1) en claro a un panel que exige cifrado -> NAK
            r = await _send(port, [dc09.build_message("SIA-DCS", "1234", "#1234|Nri1/BA3")])
            assert b'"NAK"' in r[0]
            # 2) doble zona cifrada -> alarma confirmada P1
            r = await _send(port, [dc09.build_message("SIA-DCS", "1234", "#1234|Nri1/BA3", 1, key=KEY),
                                   dc09.build_message("SIA-DCS", "1234", "#1234|Nri1/BA5", 2, key=KEY)])
            assert all(b'"*ACK"' in x for x in r)
            await asyncio.sleep(0.2)
            # 3) zona única -> se cierra sola tras el timeout
            await asyncio.sleep(0.1)
            r = await _send(port, [dc09.build_message("SIA-DCS", "1234", "#1234|Nri1/BA7", 3, key=KEY)])
            # 4) recuento
            await _send(port, [dc09.build_message("SIA-DCS", "2222", "#2222|Nri1/BA1", 1),
                               dc09.build_message("SIA-DCS", "2222", "#2222|Nri1/BA1", 2)])
            # 5) fail-safe
            await _send(port, [dc09.build_message("ADM-CID", "3333", "#3333|1130 01 001", 1)])
            # 6) desconocido
            await _send(port, [dc09.build_message("SIA-DCS", "9999", "#9999|Nri1/FA1", 1)])
            await asyncio.sleep(1.0)
        finally:
            for s in servers:
                s.close()
            await processor.stop()

    asyncio.run(scenario())

    with session_scope() as db:
        alarms = {a.account: [] for a in db.query(Alarm)}
        for a in db.query(Alarm).order_by(Alarm.id):
            alarms[a.account].append(a)
        a1 = alarms["1234"]
        # la primera (doble zona) liberada con P1; la BA7 es otra señal: si la primera sigue abierta se agrupa
        assert a1[0].status == "new" and a1[0].priority == 1 and a1[0].event_count >= 2
        a2 = alarms["2222"][0]
        assert "rafaga" in a2.tags
        a3 = alarms["3333"][0]
        assert a3.status == "new"  # liberada por fail-safe
        assert alarms["9999"][0].client_id is None
        assert db.query(Event).filter_by(account="1234").count() == 3
        assert db.query(WorkflowRun).filter(WorkflowRun.status != "done").count() == 0


def test_single_knock_closes(setup_db):
    from alarmhub.engine.processor import processor
    from alarmhub.protocols.normalized import IncomingMessage, from_sia

    async def scenario():
        with session_scope() as db:
            for a in db.query(Alarm).filter_by(account="1234"):
                a.status = "closed"
        await processor.start()
        await processor.submit(IncomingMessage(protocol="SIA-DCS", account="1234", events=[from_sia("BA", zone="3")]))
        await asyncio.sleep(1.0)
        await processor.stop()

    asyncio.run(scenario())
    with session_scope() as db:
        a = db.query(Alarm).filter_by(account="1234").order_by(Alarm.id.desc()).first()
        assert a.status == "closed" and a.resolution == "auto"


def test_trigger_matching():
    ctx = {"category": "alarm", "code": "E130", "cid_code": "130", "account": "1234", "zone": "3",
           "priority": 2, "client_group": "norte", "test_mode": False}
    assert matches({"codes": ["130"]}, ctx)
    assert matches({"codes": ["E1*"], "client_groups": ["norte"]}, ctx)
    assert not matches({"codes": ["BA"]}, ctx)
    assert not matches({"categories": ["fire"]}, ctx)
    assert matches({"fields": [{"field": "zone", "op": "in", "value": "1,3"}]}, ctx)
    assert not matches({}, {**ctx, "test_mode": True})


def test_web(setup_db):
    from alarmhub.main import app
    with TestClient(app) as c:
        tok = re.search(r'name="csrf" value="([^"]+)"', c.get("/login").text).group(1)
        r = c.post("/login", data={"csrf": tok, "username": "admin", "password": "Admin12345"},
                   follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/"
        for path in ["/", "/alarms", "/alarms?status=closed", "/events", "/clients", "/clients/1", "/panels",
                     "/panels/1", "/unknown", "/workflows", "/workflows/1", "/users", "/roles", "/settings",
                     "/audit", "/notifications", "/alarms/1", "/events/1", "/events/export.csv"]:
            assert c.get(path).status_code == 200, path
        tok = re.search(r'name="csrf" value="([^"]+)"', c.get("/clients/new").text).group(1)
        r = c.post("/clients/new", data={"csrf": tok, "code": "T2", "name": "Nuevo", "active": "on"})
        assert r.status_code == 200 and "Nuevo" in r.text
        # CSRF obligatorio
        assert c.post("/clients/new", data={"code": "T3", "name": "x"}).status_code == 400
        # API sin clave
        assert c.post("/api/v1/ingest", json={"account": "1", "code": "BA"}, cookies={}).status_code in (200, 401)
