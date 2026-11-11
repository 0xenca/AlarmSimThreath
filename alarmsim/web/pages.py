"""Páginas HTML."""
from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core import receiver, settings
from ..core.runner import runner
from ..core.security import hash_password, new_api_key, verify_password
from ..db import get_db
from ..models import Panel, PanelUser, Partition, ReceivedMessage, Scenario, SentMessage, Target, User, Zone
from ..protocol import dc09
from ..protocol.codes import CID, SIA
from ..protocol.xdata import XFIELDS
from .deps import b, checked_form, current_user, flash, render, s

router = APIRouter()
auth = [Depends(current_user)]


# ------------------------------------------------------------------ acceso
@router.get("/login")
def login_form(request: Request):
    return render(request, "login.html")


@router.post("/login")
def login(request: Request, form: dict = Depends(checked_form), db: Session = Depends(get_db)):
    u = db.query(User).filter_by(username=s(form.get("username"))).first()
    if not u or not verify_password(form.get("password", ""), u.password_hash):
        flash(request, "Usuario o contraseña incorrectos", "danger")
        return RedirectResponse("/login", 303)
    request.session.clear()
    request.session["uid"] = u.id
    return RedirectResponse("/account" if u.must_change_password else "/", 303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", 303)


@router.get("/account", dependencies=auth)
def account(request: Request):
    return render(request, "account.html")


@router.post("/account")
def account_save(request: Request, form: dict = Depends(checked_form), user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    if user is None:
        return RedirectResponse("/", 303)
    user = db.get(User, user.id)
    if form.get("new_password"):
        if not verify_password(form.get("current_password", ""), user.password_hash):
            flash(request, "Contraseña actual incorrecta", "danger")
        elif len(form["new_password"]) < 8 or form["new_password"] != form.get("new_password2"):
            flash(request, "La contraseña debe tener 8 caracteres o más y coincidir", "danger")
        else:
            user.password_hash = hash_password(form["new_password"])
            user.must_change_password = False
            flash(request, "Contraseña cambiada")
    if form.get("api_key") == "new":
        user.api_key = new_api_key()
        flash(request, "API key generada")
    elif form.get("api_key") == "revoke":
        user.api_key = None
    db.commit()
    return RedirectResponse("/account", 303)


# ------------------------------------------------------------------ generador
@router.get("/", dependencies=auth)
def generator(request: Request, panel: int | None = None, db: Session = Depends(get_db)):
    panels = db.query(Panel).order_by(Panel.account).all()
    return render(request, "generator.html", panels=panels, selected=panel or (panels[0].id if panels else None),
                  sia=SIA, cid=CID, xfields=XFIELDS, receiver_status=receiver.STATUS)


# ------------------------------------------------------------------ histórico
@router.get("/history", dependencies=auth)
def history(request: Request, page: int = 1, account: str = "", result: str = "", source: str = "",
            db: Session = Depends(get_db)):
    q = db.query(SentMessage)
    if account:
        q = q.filter(SentMessage.account.ilike(f"%{account}%"))
    if result:
        q = q.filter(SentMessage.result == result)
    if source:
        q = q.filter(SentMessage.source == source)
    total = q.count()
    items = q.order_by(SentMessage.id.desc()).offset((page - 1) * 100).limit(100).all()
    return render(request, "history.html", items=items, total=total, page=page, account=account, result=result,
                  source=source)


@router.get("/history.csv", dependencies=auth)
def history_csv(db: Session = Depends(get_db)):
    def gen():
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["id", "fecha_utc", "origen", "abonado", "destino", "token", "secuencia", "resumen", "cifrado",
                    "resultado", "latencia_ms", "intentos", "error", "trama_claro", "trama", "respuesta"])
        yield buf.getvalue()
        for m in db.query(SentMessage).order_by(SentMessage.id.desc()).yield_per(1000):
            buf.seek(0)
            buf.truncate()
            w.writerow([m.id, m.created_at.isoformat(), m.source, m.account, m.target, m.token, m.sequence, m.summary,
                        "si" if m.encrypted else "no", m.result, m.latency_ms, m.attempts, m.error, m.plaintext,
                        m.frame, m.response])
            yield buf.getvalue()
    return StreamingResponse(gen(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=historico_envios.csv"})


@router.post("/history/clear", dependencies=auth)
def history_clear(request: Request, form: dict = Depends(checked_form), db: Session = Depends(get_db)):
    db.query(SentMessage).delete()
    db.commit()
    flash(request, "Histórico vaciado")
    return RedirectResponse("/history", 303)


# ------------------------------------------------------------------ destinos
@router.get("/targets", dependencies=auth)
def targets(request: Request, edit: int | None = None, db: Session = Depends(get_db)):
    items = db.query(Target).order_by(Target.name).all()
    t = db.get(Target, edit) if edit else None
    return render(request, "targets.html", items=items, t=t)


@router.post("/targets", dependencies=auth)
def target_save(request: Request, form: dict = Depends(checked_form), db: Session = Depends(get_db)):
    tid = form.get("id")
    t = db.get(Target, int(tid)) if tid else Target()
    if t is None:
        raise HTTPException(404)
    if form.get("delete"):
        db.delete(t)
        db.commit()
        flash(request, "Destino eliminado")
        return RedirectResponse("/targets", 303)
    t.name = s(form.get("name")) or "Receptora"
    t.host = s(form.get("host"))
    t.port = int(form.get("port") or 7000)
    t.transport = "udp" if form.get("transport") == "udp" else "tcp"
    t.receiver = s(form.get("receiver")).upper()
    t.line = s(form.get("line")).upper() or "0"
    t.enc_key = s(form.get("enc_key"))
    t.key_format = form.get("key_format") or "hex"
    t.timeout = float(form.get("timeout") or 10)
    t.retries = int(form.get("retries") or 0)
    t.include_timestamp = b(form.get("include_timestamp"))
    t.keep_alive = b(form.get("keep_alive"))
    t.notes = s(form.get("notes"))
    try:
        dc09.parse_key(t.enc_key, t.key_format)
    except ValueError as exc:
        flash(request, str(exc), "danger")
        return RedirectResponse(f"/targets?edit={tid}" if tid else "/targets", 303)
    if not t.host:
        flash(request, "El host es obligatorio", "danger")
        return RedirectResponse("/targets", 303)
    db.add(t)
    db.commit()
    flash(request, "Destino guardado")
    return RedirectResponse("/targets", 303)


# ------------------------------------------------------------------ paneles
@router.get("/panels", dependencies=auth)
def panels(request: Request, db: Session = Depends(get_db)):
    return render(request, "panels.html", items=db.query(Panel).order_by(Panel.account).all())


@router.get("/panels/new", dependencies=auth)
def panel_new(request: Request, db: Session = Depends(get_db)):
    return panel_form(request, None, db)


@router.get("/panels/{pid}", dependencies=auth)
def panel_form(request: Request, pid: int | None = None, db: Session = Depends(get_db)):
    p = db.get(Panel, pid) if pid else None
    if pid and not p:
        raise HTTPException(404)
    return render(request, "panel_form.html", p=p, targets=db.query(Target).order_by(Target.name).all(),
                  xfields=XFIELDS, sia=SIA, cid=CID)


@router.post("/panels/save", dependencies=auth)
def panel_save(request: Request, form: dict = Depends(checked_form), db: Session = Depends(get_db)):
    pid = form.get("id")
    p = db.get(Panel, int(pid)) if pid else Panel(sequence=0)
    if p is None:
        raise HTTPException(404)
    back = f"/panels/{pid}" if pid else "/panels/new"
    if form.get("delete"):
        db.delete(p)
        db.commit()
        flash(request, "Panel eliminado")
        return RedirectResponse("/panels", 303)
    p.name = s(form.get("name"))
    p.account = s(form.get("account")).upper()
    p.target_id = int(form["target_id"]) if form.get("target_id") else None
    p.protocol = "ADM-CID" if form.get("protocol") == "ADM-CID" else "SIA-DCS"
    p.enc_key = s(form.get("enc_key"))
    p.key_format = form.get("key_format") or "hex"
    p.encryption = form.get("encryption") if form.get("encryption") in ("auto", "on", "off") else "auto"
    p.receiver = s(form.get("receiver")).upper()
    p.line = s(form.get("line")).upper()
    p.null_interval = int(form.get("null_interval") or 0)
    p.test_interval = int(form.get("test_interval") or 0)
    p.schedules_enabled = b(form.get("schedules_enabled"))
    p.notes = s(form.get("notes"))
    if form.get("sequence") not in (None, ""):
        p.sequence = max(0, min(9999, int(form["sequence"])))
    p.xdata_defaults = {k: s(form.get(f"x_{k}")) for k in XFIELDS if s(form.get(f"x_{k}"))}
    import re
    if not re.fullmatch(r"[0-9A-F]{3,16}", p.account):
        flash(request, "El abonado debe tener de 3 a 16 caracteres hexadecimales", "danger")
        return RedirectResponse(back, 303)
    try:
        dc09.parse_key(p.enc_key, p.key_format)
    except ValueError as exc:
        flash(request, str(exc), "danger")
        return RedirectResponse(back, 303)
    db.add(p)
    db.flush()
    if not pid and b(form.get("create_defaults")):
        db.add(Partition(panel_id=p.id, number=1, name="General"))
        for n, (name, code, cid) in enumerate([("Puerta principal", "BA", "134"), ("Volumétrico salón", "BA", "132"),
                                               ("Ventana cocina", "BA", "131"), ("Detector humo", "FA", "111"),
                                               ("Pulsador pánico", "PA", "120")], 1):
            db.add(Zone(panel_id=p.id, number=n, name=name, partition="1", alarm_code=code, cid_code=cid))
        db.add(PanelUser(panel_id=p.id, number=1, name="Titular"))
    db.commit()
    flash(request, "Panel guardado")
    return RedirectResponse(f"/panels/{p.id}", 303)


@router.post("/panels/{pid}/items", dependencies=auth)
def panel_items(pid: int, request: Request, form: dict = Depends(checked_form), db: Session = Depends(get_db)):
    p = db.get(Panel, pid)
    if not p:
        raise HTTPException(404)
    kind = form.get("kind")
    model = {"zone": Zone, "partition": Partition, "user": PanelUser}.get(kind)
    if model is None:
        raise HTTPException(400)
    if form.get("delete_id"):
        obj = db.get(model, int(form["delete_id"]))
        if obj and obj.panel_id == p.id:
            db.delete(obj)
    elif form.get("bulk"):
        # alta masiva de zonas: "1-16" o "1,2,5"
        nums: list[int] = []
        for part in s(form.get("bulk")).split(","):
            if "-" in part:
                a, _, z = part.partition("-")
                nums += list(range(int(a), int(z) + 1))
            elif part.strip():
                nums.append(int(part))
        existing = {z.number for z in p.zones}
        for n in nums[:999]:
            if n not in existing:
                db.add(Zone(panel_id=p.id, number=n, name=f"Zona {n}", partition=s(form.get("partition")) or "1"))
    else:
        obj = db.get(model, int(form["id"])) if form.get("id") else model(panel_id=p.id)
        obj.number = int(form.get("number") or 0)
        obj.name = s(form.get("name"))
        if kind == "zone":
            obj.partition = s(form.get("partition")) or "1"
            obj.alarm_code = s(form.get("alarm_code")).upper() or "BA"
            obj.cid_code = s(form.get("cid_code")) or "130"
            obj.room = s(form.get("room"))
        db.add(obj)
    try:
        db.commit()
    except (IntegrityError, ValueError):
        db.rollback()
        flash(request, "Datos no válidos", "danger")
    return RedirectResponse(f"/panels/{pid}#{kind}s", 303)


# ------------------------------------------------------------------ escenarios
EXAMPLE_STEPS = [
    {"type": "event", "spec": {"code": "BA", "zone": "1", "partition": "1",
                               "xdata": {"I": "Primera detección"}}},
    {"type": "wait", "seconds": 5},
    {"type": "event", "spec": {"code": "BA", "zone": "2", "partition": "1",
                               "xdata": {"I": "Confirmación", "V": "https://video.example.com/clip/1"}}},
    {"type": "wait", "seconds": 30},
    {"type": "event", "spec": {"code": "OP", "user": "1", "partition": "1"}},
    {"type": "null"},
]


@router.get("/scenarios", dependencies=auth)
def scenarios(request: Request, edit: int | None = None, db: Session = Depends(get_db)):
    sc = db.get(Scenario, edit) if edit else None
    return render(request, "scenarios.html", items=db.query(Scenario).order_by(Scenario.name).all(), sc=sc,
                  panels=db.query(Panel).order_by(Panel.account).all(),
                  steps_json=json.dumps(sc.steps if sc else EXAMPLE_STEPS, indent=2, ensure_ascii=False),
                  jobs=[j.as_dict(short=True) for j in reversed(list(runner.jobs.values()))])


@router.post("/scenarios", dependencies=auth)
def scenario_save(request: Request, form: dict = Depends(checked_form), db: Session = Depends(get_db)):
    sid = form.get("id")
    sc = db.get(Scenario, int(sid)) if sid else Scenario()
    if sc is None:
        raise HTTPException(404)
    if form.get("delete"):
        db.delete(sc)
        db.commit()
        return RedirectResponse("/scenarios", 303)
    try:
        steps = json.loads(form.get("steps") or "[]")
        assert isinstance(steps, list) and all(isinstance(x, dict) for x in steps)
    except (json.JSONDecodeError, AssertionError):
        flash(request, "Los pasos deben ser una lista JSON de objetos", "danger")
        return RedirectResponse(f"/scenarios?edit={sid}" if sid else "/scenarios", 303)
    sc.name = s(form.get("name")) or "Escenario"
    sc.description = s(form.get("description"))
    sc.panel_id = int(form["panel_id"]) if form.get("panel_id") else None
    sc.steps = steps
    sc.repeat = int(form.get("repeat") or 1)
    sc.repeat_delay = float(form.get("repeat_delay") or 0)
    db.add(sc)
    db.commit()
    flash(request, "Escenario guardado")
    return RedirectResponse(f"/scenarios?edit={sc.id}", 303)


# ------------------------------------------------------------------ carga, trama manual, receptor
@router.get("/load", dependencies=auth)
def load_page(request: Request, db: Session = Depends(get_db)):
    return render(request, "load.html", targets=db.query(Target).order_by(Target.name).all(),
                  jobs=[j.as_dict(short=True) for j in reversed(list(runner.jobs.values())) if j.kind == "load"])


@router.get("/raw", dependencies=auth)
def raw_page(request: Request, db: Session = Depends(get_db)):
    return render(request, "raw.html", targets=db.query(Target).order_by(Target.name).all())


@router.get("/receiver", dependencies=auth)
def receiver_page(request: Request, db: Session = Depends(get_db)):
    items = db.query(ReceivedMessage).order_by(ReceivedMessage.id.desc()).limit(200).all()
    return render(request, "receiver.html", items=items, status=receiver.STATUS,
                  mode=settings.get("receiver_mode"), keys=settings.get("receiver_keys") or {})


@router.post("/receiver", dependencies=auth)
def receiver_save(request: Request, form: dict = Depends(checked_form), db: Session = Depends(get_db)):
    if form.get("clear"):
        db.query(ReceivedMessage).delete()
        db.commit()
        return RedirectResponse("/receiver", 303)
    settings.put("receiver_mode", form.get("mode") if form.get("mode") in ("ack", "nak", "duh", "silent") else "ack")
    keys = {}
    for line in s(form.get("keys")).splitlines():
        if "=" in line:
            acc, _, key = line.partition("=")
            try:
                dc09.parse_key(key.strip())
                keys[acc.strip().upper()] = key.strip()
            except ValueError as exc:
                flash(request, f"{acc.strip()}: {exc}", "danger")
    settings.put("receiver_keys", keys)
    flash(request, "Receptor de pruebas actualizado")
    return RedirectResponse("/receiver", 303)
