"""Clientes, contactos, paneles, zonas, usuarios de panel y abonados desconocidos."""
from __future__ import annotations

import csv
import io
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Alarm, Client, Comment, Contact, Event, Panel, PanelUser, User, Zone, utcnow
from ..protocols.dc09 import parse_key
from .deps import audit, b, can_see_client, checked_form, csrf_token, flash, render, require, s, scope

router = APIRouter()

CLIENT_FIELDS = ["code", "name", "group", "tax_id", "client_type", "address", "city", "postal_code", "province",
                 "country", "phone", "email", "keyword", "duress_keyword", "instructions", "notes", "contract_number"]
PANEL_FIELDS = ["name", "account", "line_prefix", "protocol", "manufacturer", "model", "serial", "installer",
                "location", "enc_key", "key_format", "notes"]


def _client(db: Session, user: User, cid: int) -> Client:
    c = db.get(Client, cid)
    if c is None or not can_see_client(user, c):
        raise HTTPException(404, "Cliente no encontrado")
    return c


def _panel(db: Session, user: User, pid: int) -> Panel:
    p = db.get(Panel, pid)
    if p is None or (p.client and not can_see_client(user, p.client)):
        raise HTTPException(404, "Panel no encontrado")
    return p


# ------------------------------------------------------------------ clientes
@router.get("/clients")
def clients_list(request: Request, q: str = "", group: str = "", page: int = 1,
                 user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    query = scope(db.query(Client), user, Client.id)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Client.name.ilike(like), Client.code.ilike(like), Client.city.ilike(like),
                                 Client.phone.ilike(like), Client.id.in_(
                                     db.query(Panel.client_id).filter(Panel.account.ilike(like)))))
    if group:
        query = query.filter(Client.group == group)
    total = query.count()
    items = query.order_by(Client.name).offset((page - 1) * 50).limit(50).all()
    groups = [g for (g,) in db.query(Client.group).distinct() if g]
    return render(request, "clients.html", items=items, q=q, group=group, groups=groups, page=page, total=total)


@router.get("/clients/new")
def client_new(request: Request, user: User = Depends(require("clients.edit")), db: Session = Depends(get_db)):
    last = db.query(func.max(Client.id)).scalar() or 0
    return render(request, "client_form.html", c=None, suggested_code=f"C{last + 1:05d}")


@router.post("/clients/new")
def client_create(request: Request, form: dict = Depends(checked_form), user: User = Depends(require("clients.edit")),
                  db: Session = Depends(get_db)):
    c = Client(**{f: s(form.get(f)) for f in CLIENT_FIELDS}, active=b(form.get("active", "on")))
    if not c.code or not c.name:
        flash(request, "Código y nombre son obligatorios", "danger")
        return RedirectResponse("/clients/new", 303)
    db.add(c)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        flash(request, "Ya existe un cliente con ese código", "danger")
        return RedirectResponse("/clients/new", 303)
    audit(db, request, user, "client_create", "client", c.id, c.name)
    db.commit()
    flash(request, "Cliente creado")
    return RedirectResponse(f"/clients/{c.id}", 303)


@router.get("/clients/{cid}")
def client_detail(cid: int, request: Request, user: User = Depends(require("clients.view")),
                  db: Session = Depends(get_db)):
    c = _client(db, user, cid)
    events = db.query(Event).filter_by(client_id=c.id).order_by(Event.id.desc()).limit(50).all()
    alarms = db.query(Alarm).filter_by(client_id=c.id).order_by(Alarm.id.desc()).limit(20).all()
    comments = (db.query(Comment).filter(Comment.client_id == c.id, Comment.alarm_id.is_(None))
                .order_by(Comment.id.desc()).all())
    return render(request, "client_detail.html", c=c, events=events, alarms=alarms, comments=comments)


@router.get("/clients/{cid}/edit")
def client_edit(cid: int, request: Request, user: User = Depends(require("clients.edit")),
                db: Session = Depends(get_db)):
    return render(request, "client_form.html", c=_client(db, user, cid), suggested_code="")


@router.post("/clients/{cid}/edit")
def client_update(cid: int, request: Request, form: dict = Depends(checked_form),
                  user: User = Depends(require("clients.edit")), db: Session = Depends(get_db)):
    c = _client(db, user, cid)
    for f in CLIENT_FIELDS:
        if f in ("keyword", "duress_keyword") and not user.has("clients.secrets") and not form.get(f):
            continue
        setattr(c, f, s(form.get(f)))
    c.active = b(form.get("active"))
    try:
        audit(db, request, user, "client_update", "client", c.id, c.name)
        db.commit()
    except IntegrityError:
        db.rollback()
        flash(request, "Código de cliente duplicado", "danger")
        return RedirectResponse(f"/clients/{cid}/edit", 303)
    flash(request, "Cliente actualizado")
    return RedirectResponse(f"/clients/{c.id}", 303)


@router.post("/clients/{cid}/delete")
def client_delete(cid: int, request: Request, form: dict = Depends(checked_form),
                  user: User = Depends(require("clients.edit")), db: Session = Depends(get_db)):
    c = _client(db, user, cid)
    audit(db, request, user, "client_delete", "client", c.id, c.name)
    db.delete(c)
    db.commit()
    flash(request, "Cliente eliminado (sus paneles quedan sin asignar)")
    return RedirectResponse("/clients", 303)


@router.post("/clients/{cid}/comment")
def client_comment(cid: int, request: Request, form: dict = Depends(checked_form),
                   user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    c = _client(db, user, cid)
    if s(form.get("text")):
        db.add(Comment(client_id=c.id, user_id=user.id, text=s(form.get("text")), kind="note"))
        db.commit()
    return RedirectResponse(f"/clients/{c.id}#notes", 303)


@router.post("/clients/{cid}/contacts")
def contact_save(cid: int, request: Request, form: dict = Depends(checked_form),
                 user: User = Depends(require("clients.edit")), db: Session = Depends(get_db)):
    c = _client(db, user, cid)
    ct_id = form.get("contact_id")
    ct = db.get(Contact, int(ct_id)) if ct_id else Contact(client_id=c.id)
    if ct is None or ct.client_id != c.id:
        raise HTTPException(404)
    if form.get("delete"):
        db.delete(ct)
    else:
        for f in ("name", "relation", "phone", "phone2", "email", "notes"):
            setattr(ct, f, s(form.get(f)))
        ct.priority = int(form.get("priority") or 1)
        ct.has_keys = b(form.get("has_keys"))
        ct.notify_email = b(form.get("notify_email"))
        if not ct.name:
            flash(request, "El contacto necesita nombre", "danger")
            return RedirectResponse(f"/clients/{c.id}#contacts", 303)
        db.add(ct)
    audit(db, request, user, "contact_save", "client", c.id, s(form.get("name")))
    db.commit()
    return RedirectResponse(f"/clients/{c.id}#contacts", 303)


@router.get("/clients-import")
def clients_import_form(request: Request, user: User = Depends(require("clients.edit"))):
    return render(request, "clients_import.html")


@router.post("/clients-import")
async def clients_import(request: Request, file: UploadFile, user: User = Depends(require("clients.edit")),
                         db: Session = Depends(get_db)):
    form = await request.form()
    if form.get("csrf") != csrf_token(request):
        raise HTTPException(400, "CSRF")
    content = (await file.read()).decode("utf-8-sig", errors="replace")
    dialect = csv.Sniffer().sniff(content.splitlines()[0]) if content else csv.excel
    reader = csv.DictReader(io.StringIO(content), dialect=dialect)
    created = updated = panels = 0
    for row in reader:
        row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        if not row.get("code") or not row.get("name"):
            continue
        c = db.query(Client).filter_by(code=row["code"]).first()
        if c is None:
            c = Client(code=row["code"], name=row["name"])
            db.add(c)
            created += 1
        else:
            updated += 1
        for f in CLIENT_FIELDS:
            if row.get(f):
                setattr(c, f, row[f])
        db.flush()
        if row.get("account"):
            p = db.query(Panel).filter_by(account=row["account"].upper(), line_prefix=row.get("line_prefix", "")).first()
            if p is None:
                p = Panel(account=row["account"].upper(), line_prefix=row.get("line_prefix", ""))
                db.add(p)
                panels += 1
            p.client_id = c.id
            for f in ("protocol", "manufacturer", "model", "enc_key"):
                if row.get(f):
                    setattr(p, f, row[f])
            if row.get("supervision_minutes"):
                p.supervision_minutes = int(row["supervision_minutes"])
    audit(db, request, user, "clients_import", "client", details=f"{created} nuevos, {updated} actualizados")
    db.commit()
    flash(request, f"Importación: {created} clientes nuevos, {updated} actualizados, {panels} paneles creados")
    return RedirectResponse("/clients", 303)


# ------------------------------------------------------------------ paneles
@router.get("/panels")
def panels_list(request: Request, q: str = "", filter: str = "", page: int = 1,
                user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    query = scope(db.query(Panel), user, Panel.client_id)
    if q:
        like = f"%{q}%"
        query = query.outerjoin(Client).filter(or_(Panel.account.ilike(like), Panel.name.ilike(like),
                                                   Client.name.ilike(like), Panel.model.ilike(like)))
    if filter == "supervision":
        query = query.filter(Panel.supervision_failed.is_(True))
    elif filter == "unassigned":
        query = query.filter(Panel.client_id.is_(None))
    elif filter == "test":
        query = query.filter(Panel.test_mode_until > utcnow())
    elif filter == "silent":
        query = query.filter(or_(Panel.last_seen.is_(None), Panel.last_seen < utcnow() - timedelta(days=1)))
    total = query.count()
    items = query.order_by(Panel.account).offset((page - 1) * 50).limit(50).all()
    return render(request, "panels.html", items=items, q=q, filter=filter, page=page, total=total, now=utcnow())


@router.get("/panels/new")
def panel_new(request: Request, client_id: int | None = None, account: str = "",
              user: User = Depends(require("clients.edit")), db: Session = Depends(get_db)):
    clients = scope(db.query(Client), user, Client.id).order_by(Client.name).all()
    return render(request, "panel_form.html", p=None, clients=clients, client_id=client_id, account=account)


def _apply_panel_form(p: Panel, form: dict, user: User) -> str | None:
    for f in PANEL_FIELDS:
        if f == "enc_key" and not user.has("clients.secrets"):
            continue
        setattr(p, f, s(form.get(f)))
    p.account = p.account.upper()
    p.client_id = int(form["client_id"]) if form.get("client_id") else None
    p.supervision_minutes = int(form.get("supervision_minutes") or 0)
    p.require_encryption = b(form.get("require_encryption"))
    p.active = b(form.get("active", "on"))
    for f in ("latitude", "longitude"):
        try:
            setattr(p, f, float(form[f]) if form.get(f) else None)
        except ValueError:
            return f"{f} no es un número"
    if not p.account:
        return "El número de abonado es obligatorio"
    if p.enc_key:
        try:
            parse_key(p.enc_key, p.key_format)
        except ValueError as exc:
            return str(exc)
    return None


@router.post("/panels/new")
def panel_create(request: Request, form: dict = Depends(checked_form), user: User = Depends(require("clients.edit")),
                 db: Session = Depends(get_db)):
    p = Panel()
    err = _apply_panel_form(p, form, user)
    if err:
        flash(request, err, "danger")
        return RedirectResponse("/panels/new", 303)
    db.add(p)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        flash(request, "Ya existe un panel con ese abonado y prefijo", "danger")
        return RedirectResponse("/panels/new", 303)
    # Vincula señales previas del abonado desconocido
    linked = (db.query(Event).filter(Event.panel_id.is_(None), Event.account == p.account)
              .update({Event.panel_id: p.id, Event.client_id: p.client_id}, synchronize_session=False))
    audit(db, request, user, "panel_create", "panel", p.id, p.account)
    db.commit()
    flash(request, f"Panel creado{f' ({linked} señales previas vinculadas)' if linked else ''}")
    return RedirectResponse(f"/panels/{p.id}", 303)


@router.get("/panels/{pid}")
def panel_detail(pid: int, request: Request, user: User = Depends(require("clients.view")),
                 db: Session = Depends(get_db)):
    p = _panel(db, user, pid)
    events = db.query(Event).filter_by(panel_id=p.id).order_by(Event.id.desc()).limit(100).all()
    return render(request, "panel_detail.html", p=p, events=events, now=utcnow())


@router.get("/panels/{pid}/edit")
def panel_edit(pid: int, request: Request, user: User = Depends(require("clients.edit")),
               db: Session = Depends(get_db)):
    p = _panel(db, user, pid)
    clients = scope(db.query(Client), user, Client.id).order_by(Client.name).all()
    return render(request, "panel_form.html", p=p, clients=clients, client_id=p.client_id, account=p.account)


@router.post("/panels/{pid}/edit")
def panel_update(pid: int, request: Request, form: dict = Depends(checked_form),
                 user: User = Depends(require("clients.edit")), db: Session = Depends(get_db)):
    p = _panel(db, user, pid)
    err = _apply_panel_form(p, form, user)
    if err:
        db.rollback()
        flash(request, err, "danger")
        return RedirectResponse(f"/panels/{pid}/edit", 303)
    try:
        audit(db, request, user, "panel_update", "panel", p.id, p.account)
        db.commit()
    except IntegrityError:
        db.rollback()
        flash(request, "Ya existe un panel con ese abonado y prefijo", "danger")
        return RedirectResponse(f"/panels/{pid}/edit", 303)
    flash(request, "Panel actualizado")
    return RedirectResponse(f"/panels/{p.id}", 303)


@router.post("/panels/{pid}/delete")
def panel_delete(pid: int, request: Request, form: dict = Depends(checked_form),
                 user: User = Depends(require("clients.edit")), db: Session = Depends(get_db)):
    p = _panel(db, user, pid)
    audit(db, request, user, "panel_delete", "panel", p.id, p.account)
    db.delete(p)
    db.commit()
    flash(request, "Panel eliminado")
    return RedirectResponse("/panels", 303)


@router.post("/panels/{pid}/test-mode")
def panel_test_mode(pid: int, request: Request, form: dict = Depends(checked_form),
                    user: User = Depends(require("clients.edit")), db: Session = Depends(get_db)):
    p = _panel(db, user, pid)
    minutes = int(form.get("minutes") or 0)
    p.test_mode_until = utcnow() + timedelta(minutes=minutes) if minutes > 0 else None
    audit(db, request, user, "panel_test_mode", "panel", p.id, f"{minutes} min")
    db.commit()
    flash(request, f"Modo prueba {'activado ' + str(minutes) + ' min' if minutes else 'desactivado'}")
    return RedirectResponse(f"/panels/{p.id}", 303)


@router.post("/panels/{pid}/zones")
def zone_save(pid: int, request: Request, form: dict = Depends(checked_form),
              user: User = Depends(require("clients.edit")), db: Session = Depends(get_db)):
    p = _panel(db, user, pid)
    kind = form.get("kind", "zone")
    model = Zone if kind == "zone" else PanelUser
    obj_id = form.get("id")
    obj = db.get(model, int(obj_id)) if obj_id else model(panel_id=p.id)
    if obj is None or obj.panel_id != p.id:
        raise HTTPException(404)
    if form.get("delete"):
        db.delete(obj)
    else:
        obj.number = s(form.get("number")).lstrip("0") or "0"
        obj.name = s(form.get("name"))
        if kind == "zone":
            obj.zone_type = s(form.get("zone_type"))
            obj.partition = s(form.get("partition"))
            obj.notes = s(form.get("notes"))
        db.add(obj)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        flash(request, "Número duplicado", "danger")
    return RedirectResponse(f"/panels/{p.id}#{'zones' if kind == 'zone' else 'users'}", 303)


@router.get("/unknown")
def unknown_accounts(request: Request, user: User = Depends(require("clients.view")), db: Session = Depends(get_db)):
    rows = (db.query(Event.account, Event.line_prefix, func.count(Event.id), func.min(Event.received_at),
                     func.max(Event.received_at), func.max(Event.remote))
            .filter(Event.panel_id.is_(None), Event.protocol != "INTERNAL")
            .group_by(Event.account, Event.line_prefix).order_by(func.max(Event.received_at).desc()).all())
    return render(request, "unknown.html", rows=rows)
