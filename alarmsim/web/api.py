"""API JSON (usada por la interfaz y por integraciones con cabecera X-API-Key)."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..core import receiver, service
from ..core.bus import bus
from ..core.runner import runner
from ..db import get_db
from ..models import Panel, SentMessage, Template
from ..protocol import dc09
from ..protocol.codes import CID, CID_QUALIFIERS, SIA
from ..protocol.xdata import XFIELDS
from .deps import check_json_csrf, current_user

router = APIRouter(prefix="/api", tags=["api"], dependencies=[Depends(current_user)])


def _err(exc: Exception) -> HTTPException:
    return HTTPException(400, str(exc))


@router.get("/codes")
def codes():
    return {"sia": {k: {"desc": v[0], "cat": v[1], "addr": v[2]} for k, v in SIA.items()},
            "cid": {k: {"desc": v[0], "cat": v[1], "addr": v[2]} for k, v in CID.items()},
            "cid_qualifiers": CID_QUALIFIERS,
            "xdata": {k: {"name": f.name, "desc": f.description, "max": f.max_len, "ph": f.placeholder}
                      for k, f in XFIELDS.items()}}


@router.get("/panels")
def panels(db: Session = Depends(get_db)):
    return [{"id": p.id, "account": p.account, "name": p.name, "protocol": p.protocol,
             "target": p.target.label if p.target else None, "sequence": p.sequence,
             "encryption": p.encryption, "has_key": bool(p.enc_key or (p.target and p.target.enc_key)),
             "zones": [{"number": z.number, "name": z.name, "partition": z.partition, "code": z.alarm_code,
                        "cid": z.cid_code, "room": z.room} for z in p.zones],
             "partitions": [{"number": x.number, "name": x.name} for x in p.partitions],
             "users": [{"number": u.number, "name": u.name} for u in p.users]}
            for p in db.query(Panel).order_by(Panel.account)]


@router.post("/preview", dependencies=[Depends(check_json_csrf)])
def preview(payload: dict[str, Any] = Body(...)):
    try:
        return service.preview(int(payload["panel_id"]), payload.get("spec") or {})
    except (dc09.DC09Error, ValueError, KeyError) as exc:
        raise _err(exc) from exc


@router.post("/send", dependencies=[Depends(check_json_csrf)])
async def send(payload: dict[str, Any] = Body(..., examples=[{
        "panel_id": 1, "spec": {"code": "BA", "zone": "3", "partition": "1",
                                "xdata": {"I": "Puerta principal", "V": "https://video/clip/1"}}}])):
    """Envía una alarma. ``count`` > 1 repite el envío (``interval`` segundos entre envíos)."""
    try:
        count = max(1, min(int(payload.get("count", 1)), 10000))
        interval = float(payload.get("interval", 0))
        results = []
        for i in range(count):
            results.append(await service.send_spec(int(payload["panel_id"]), payload.get("spec") or {},
                                                   payload.get("source", "manual")))
            if i < count - 1 and interval:
                await asyncio.sleep(interval)
        return results[0] if count == 1 else {"results": results}
    except (dc09.DC09Error, ValueError, KeyError) as exc:
        raise _err(exc) from exc


@router.post("/raw", dependencies=[Depends(check_json_csrf)])
async def raw(payload: dict[str, Any] = Body(...)):
    try:
        return await service.send_raw(int(payload["target_id"]), payload.get("body", ""), payload.get("key", ""),
                                      bool(payload.get("fix_crc", True)))
    except (dc09.DC09Error, ValueError, KeyError) as exc:
        raise _err(exc) from exc


@router.post("/templates", dependencies=[Depends(check_json_csrf)])
def save_template(payload: dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    t = Template(name=str(payload.get("name") or "Plantilla")[:128], spec=payload.get("spec") or {})
    db.add(t)
    db.commit()
    return {"id": t.id}


@router.get("/templates")
def list_templates(db: Session = Depends(get_db)):
    return [{"id": t.id, "name": t.name, "spec": t.spec} for t in db.query(Template).order_by(Template.name)]


@router.delete("/templates/{tid}", dependencies=[Depends(check_json_csrf)])
def delete_template(tid: int, db: Session = Depends(get_db)):
    t = db.get(Template, tid)
    if t:
        db.delete(t)
        db.commit()
    return {"ok": True}


@router.post("/scenarios/{sid}/run", dependencies=[Depends(check_json_csrf)])
async def run_scenario(sid: int, payload: dict[str, Any] = Body(default={})):
    try:
        return runner.start_scenario(sid, payload.get("panel_id")).as_dict()
    except ValueError as exc:
        raise _err(exc) from exc


@router.post("/load", dependencies=[Depends(check_json_csrf)])
async def load(payload: dict[str, Any] = Body(...)):
    if not payload.get("target_id"):
        raise HTTPException(400, "Seleccione un destino")
    return runner.start_load(payload).as_dict()


@router.get("/jobs")
def jobs():
    return [j.as_dict(short=True) for j in reversed(list(runner.jobs.values()))]


@router.get("/jobs/{job_id}")
def job(job_id: str):
    j = runner.jobs.get(job_id)
    if not j:
        raise HTTPException(404)
    return j.as_dict()


@router.post("/jobs/{job_id}/stop", dependencies=[Depends(check_json_csrf)])
async def stop_job(job_id: str):
    return {"stopped": runner.stop(job_id)}


@router.get("/history/{mid}")
def history_item(mid: int, db: Session = Depends(get_db)):
    m = db.get(SentMessage, mid)
    if not m:
        raise HTTPException(404)
    return {c.name: getattr(m, c.name) for c in SentMessage.__table__.columns}


@router.get("/status")
def status():
    return {"receiver": receiver.STATUS, "jobs": len([j for j in runner.jobs.values() if j.status == "running"])}


@router.get("/stream")
async def stream(request: Request):
    q = bus.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield f"data: {payload}\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
