"""Integración: web + API + envío real al receptor de pruebas."""
import asyncio
import re
import socket

from fastapi.testclient import TestClient

from alarmsim.bootstrap import init_db
from alarmsim.core import receiver, service
from alarmsim.db import session_scope
from alarmsim.models import Panel, ReceivedMessage, Target


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_send_to_test_receiver():
    init_db("Admin12345")
    port = free_port()
    with session_scope() as db:
        t = db.query(Target).first()
        t.port = port
        t.enc_key = "000102030405060708090A0B0C0D0E0F"
        t.retries = 1
        t.timeout = 2

    async def scenario():
        servers = await receiver.start("127.0.0.1", port)
        try:
            out = [await service.send_spec(1, {"code": "BA", "zone": "2", "partition": "1",
                                               "xdata": {"V": "https://x/1"}}),
                   await service.send_spec(1, {"token": "ADM-CID", "code": "130", "qualifier": "E", "zone": "3"}),
                   await service.send_spec(1, {"token": "NULL"}),
                   await service.send_spec(1, {"code": "BA", "options": {"encryption": "wrong_key"}}),
                   await service.send_spec(1, {"code": "BA", "options": {"encryption": "off"}})]
        finally:
            for srv in servers:
                srv.close()
        return out

    res = asyncio.run(scenario())
    assert [r["result"] for r in res] == ["ACK", "ACK", "ACK", "NAK", "ACK"]
    assert res[0]["encrypted"] and not res[4]["encrypted"]
    assert res[3]["attempts"] == 2          # reintento tras NAK
    assert [r["sequence"] for r in res] == ["0001", "0002", "0003", "0004", "0005"]
    with session_scope() as db:
        first = db.query(ReceivedMessage).order_by(ReceivedMessage.id).first()
        assert first.decoded["xdata"]["V"] == "https://x/1"
        assert first.decoded["xdata"]["I"] == "Volumétrico salón"     # nombre de zona automático
        assert db.get(Panel, 1).sequence == 5


def test_timeout_without_receiver():
    with session_scope() as db:
        t = db.query(Target).first()
        t.port = free_port()
        t.retries = 0
    r = asyncio.run(service.send_spec(1, {"code": "RP"}))
    assert r["result"] in ("ERROR", "TIMEOUT")


def test_web_pages_and_api():
    from alarmsim.main import app
    with TestClient(app) as c:
        tok = re.search(r'name="csrf" value="([^"]+)"', c.get("/login").text).group(1)
        assert c.post("/login", data={"csrf": tok, "username": "admin", "password": "Admin12345"},
                      follow_redirects=False).headers["location"] == "/"
        for path in ["/", "/history", "/history.csv", "/panels", "/panels/new", "/panels/1", "/targets",
                     "/targets?edit=1", "/scenarios", "/scenarios?edit=1", "/load", "/raw", "/receiver", "/account",
                     "/api/panels", "/api/codes", "/api/jobs", "/api/status"]:
            assert c.get(path).status_code == 200, path
        tok = re.search(r'name="csrf" content="([^"]+)"', c.get("/").text).group(1)
        p = c.post("/api/preview", json={"panel_id": 1, "spec": {"code": "FA", "zone": "5", "xdata": {"I": "Humo"}}},
                   headers={"X-CSRF": tok}).json()
        assert "[IHumo]" in p["plaintext"] and p["encrypted"]
        assert c.post("/api/preview", json={"panel_id": 1, "spec": {"code": "FA"}}).status_code == 400  # sin CSRF
        bad = c.post("/api/preview", json={"panel_id": 1, "spec": {"code": "F1"}}, headers={"X-CSRF": tok})
        assert bad.status_code == 400 and "inválido" in bad.json()["detail"]
