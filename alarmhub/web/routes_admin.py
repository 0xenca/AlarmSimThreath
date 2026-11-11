"""Workflows, usuarios, perfiles, configuración, auditoría y notificaciones."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import settings_store
from ..db import get_db
from ..engine import notify
from ..engine.context import matches
from ..engine.workflows import STEP_DOCS, engine, validate_definition
from ..models import (PERMISSIONS, AuditLog, NotificationLog, Role, User, Workflow, WorkflowRun)
from ..protocols.codes import CATEGORIES
from ..security import hash_password, new_api_key
from .deps import audit, b, checked_form, flash, render, require, s

router = APIRouter()

TRIGGER_DOCS = {
    "categories": "Lista de categorías: " + ", ".join(CATEGORIES),
    "codes": "Códigos SIA (BA, FA...) o Contact ID (E130, R401, 130). Admite comodines: B*, E1*",
    "exclude_codes": "Códigos a excluir",
    "accounts": "Abonados (admite comodines: 12*)",
    "client_ids / client_groups": "Clientes concretos o grupos de clientes",
    "zones / partitions": "Zonas o particiones",
    "protocols": "SIA-DCS, ADM-CID, SURGARD, HTTP, INTERNAL",
    "armed_state": "'armado' o 'desarmado' (estado del panel)",
    "max_priority": "Solo eventos con prioridad <= N (1 = más grave)",
    "unknown_account": "true = solo abonados no dados de alta",
    "schedule / not_schedule": "{\"days\": [0..6 (0=lunes)], \"from\": \"22:00\", \"to\": \"06:00\"}",
    "text_regex": "Expresión regular sobre la descripción",
    "fields": "[{\"field\": \"zone_name\", \"op\": \"regex|eq|ne|in|not_in|gt|lt|empty\", \"value\": \"...\"}]",
    "exclude_test": "Por defecto true: ignora paneles en modo prueba",
}

PLACEHOLDERS = ["event_id", "received_at", "account", "code", "cid_code", "description", "category",
                "category_name", "priority", "zone", "zone_name", "partition", "user", "user_name", "text",
                "protocol", "remote", "panel_name", "panel_location", "armed_state", "client_code", "client_name",
                "client_group", "client_address", "client_phone", "client_email", "alarm_id", "count",
                "matched_code", "matched_zone", "matched_received_at"]


# ------------------------------------------------------------------ workflows
@router.get("/workflows")
def workflows_list(request: Request, user: User = Depends(require("workflows.view")), db: Session = Depends(get_db)):
    items = db.query(Workflow).order_by(Workflow.order, Workflow.id).all()
    runs = db.query(WorkflowRun).order_by(WorkflowRun.id.desc()).limit(30).all()
    return render(request, "workflows.html", items=items, runs=runs, waiting=len(engine.waiters))


@router.get("/workflows/new")
def workflow_new(request: Request, user: User = Depends(require("workflows.edit"))):
    tpl = {"trigger": {"categories": ["alarm"]}, "hold_alarm": False,
           "steps": [STEP_DOCS["email"]["example"]]}
    return render(request, "workflow_form.html", wf=None, definition=json.dumps(tpl, indent=2, ensure_ascii=False),
                  step_docs=STEP_DOCS, trigger_docs=TRIGGER_DOCS, placeholders=PLACEHOLDERS)


@router.get("/workflows/{wid}")
def workflow_edit(wid: int, request: Request, user: User = Depends(require("workflows.view")),
                  db: Session = Depends(get_db)):
    wf = db.get(Workflow, wid)
    if not wf:
        raise HTTPException(404)
    runs = db.query(WorkflowRun).filter_by(workflow_id=wf.id).order_by(WorkflowRun.id.desc()).limit(50).all()
    return render(request, "workflow_form.html", wf=wf, runs=runs,
                  definition=json.dumps(wf.definition, indent=2, ensure_ascii=False),
                  step_docs=STEP_DOCS, trigger_docs=TRIGGER_DOCS, placeholders=PLACEHOLDERS)


@router.post("/workflows/save")
def workflow_save(request: Request, form: dict = Depends(checked_form), user: User = Depends(require("workflows.edit")),
                  db: Session = Depends(get_db)):
    wid = form.get("id")
    wf = db.get(Workflow, int(wid)) if wid else Workflow()
    if wf is None:
        raise HTTPException(404)
    try:
        definition = json.loads(form.get("definition") or "{}")
    except json.JSONDecodeError as exc:
        flash(request, f"JSON inválido: {exc}", "danger")
        return RedirectResponse(f"/workflows/{wid}" if wid else "/workflows/new", 303)
    errors = validate_definition(definition)
    if errors:
        flash(request, "Errores: " + "; ".join(errors), "danger")
        return RedirectResponse(f"/workflows/{wid}" if wid else "/workflows/new", 303)
    wf.name = s(form.get("name")) or "Sin nombre"
    wf.description = s(form.get("description"))
    wf.order = int(form.get("order") or 100)
    wf.enabled = b(form.get("enabled"))
    wf.stop_processing = b(form.get("stop_processing"))
    wf.definition = definition
    db.add(wf)
    db.flush()
    audit(db, request, user, "workflow_save", "workflow", wf.id, json.dumps(definition)[:2000])
    db.commit()
    flash(request, "Workflow guardado")
    return RedirectResponse(f"/workflows/{wf.id}", 303)


@router.post("/workflows/{wid}/toggle")
def workflow_toggle(wid: int, request: Request, form: dict = Depends(checked_form),
                    user: User = Depends(require("workflows.edit")), db: Session = Depends(get_db)):
    wf = db.get(Workflow, wid)
    if wf:
        wf.enabled = not wf.enabled
        audit(db, request, user, "workflow_toggle", "workflow", wf.id, str(wf.enabled))
        db.commit()
    return RedirectResponse("/workflows", 303)


@router.post("/workflows/{wid}/duplicate")
def workflow_duplicate(wid: int, request: Request, form: dict = Depends(checked_form),
                       user: User = Depends(require("workflows.edit")), db: Session = Depends(get_db)):
    wf = db.get(Workflow, wid)
    if not wf:
        raise HTTPException(404)
    copy = Workflow(name=wf.name + " (copia)", description=wf.description, order=wf.order, enabled=False,
                    stop_processing=wf.stop_processing, definition=json.loads(json.dumps(wf.definition)))
    db.add(copy)
    db.commit()
    return RedirectResponse(f"/workflows/{copy.id}", 303)


@router.post("/workflows/{wid}/delete")
def workflow_delete(wid: int, request: Request, form: dict = Depends(checked_form),
                    user: User = Depends(require("workflows.edit")), db: Session = Depends(get_db)):
    wf = db.get(Workflow, wid)
    if wf:
        audit(db, request, user, "workflow_delete", "workflow", wf.id, wf.name)
        db.delete(wf)
        db.commit()
    flash(request, "Workflow eliminado")
    return RedirectResponse("/workflows", 303)


@router.post("/workflows/test-trigger")
def workflow_test(request: Request, form: dict = Depends(checked_form),
                  user: User = Depends(require("workflows.view"))):
    """Comprueba si un evento de ejemplo cumpliría el trigger (sin ejecutar nada)."""
    try:
        definition = json.loads(form.get("definition") or "{}")
        sample = json.loads(form.get("sample") or "{}")
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": str(exc)}
    errors = validate_definition(definition)
    return {"ok": not errors, "errors": errors, "matches": matches(definition.get("trigger"), sample)}


@router.get("/workflow-runs/{rid}")
def workflow_run(rid: int, request: Request, user: User = Depends(require("workflows.view")),
                 db: Session = Depends(get_db)):
    r = db.get(WorkflowRun, rid)
    if not r:
        raise HTTPException(404)
    return render(request, "workflow_run.html", r=r)


@router.post("/workflow-runs/{rid}/cancel")
def workflow_run_cancel(rid: int, request: Request, form: dict = Depends(checked_form),
                        user: User = Depends(require("workflows.edit"))):
    if not engine.cancel_run(rid):
        flash(request, "La ejecución no está activa", "warning")
        return RedirectResponse(f"/workflow-runs/{rid}", 303)
    flash(request, "Cancelación solicitada")
    return RedirectResponse(f"/workflow-runs/{rid}", 303)


# ------------------------------------------------------------------ usuarios y perfiles
@router.get("/users")
def users_list(request: Request, user: User = Depends(require("users.manage")), db: Session = Depends(get_db)):
    return render(request, "users.html", items=db.query(User).order_by(User.username).all(),
                  roles=db.query(Role).order_by(Role.name).all())


@router.get("/users/new")
@router.get("/users/{uid}")
def user_form(request: Request, uid: int | None = None, user: User = Depends(require("users.manage")),
              db: Session = Depends(get_db)):
    u = db.get(User, uid) if uid else None
    if uid and not u:
        raise HTTPException(404)
    return render(request, "user_form.html", u=u, roles=db.query(Role).order_by(Role.name).all())


@router.post("/users/save")
def user_save(request: Request, form: dict = Depends(checked_form), user: User = Depends(require("users.manage")),
              db: Session = Depends(get_db)):
    uid = form.get("id")
    u = db.get(User, int(uid)) if uid else User(password_hash="")
    if u is None:
        raise HTTPException(404)
    u.username = s(form.get("username"))
    u.full_name = s(form.get("full_name"))
    u.email = s(form.get("email"))
    u.phone = s(form.get("phone"))
    u.role_id = int(form["role_id"]) if form.get("role_id") else None
    u.active = b(form.get("active"))
    u.client_groups = [g.strip() for g in s(form.get("client_groups")).split(",") if g.strip()]
    pwd = form.get("password", "")
    if pwd:
        if len(pwd) < 8:
            flash(request, "La contraseña debe tener al menos 8 caracteres", "danger")
            return RedirectResponse(f"/users/{uid}" if uid else "/users/new", 303)
        u.password_hash = hash_password(pwd)
        u.must_change_password = b(form.get("must_change_password"))
    elif not uid:
        flash(request, "La contraseña es obligatoria para usuarios nuevos", "danger")
        return RedirectResponse("/users/new", 303)
    if form.get("regen_api_key"):
        u.api_key = new_api_key()
    if form.get("revoke_api_key"):
        u.api_key = None
    if u.id == user.id and not u.active:
        flash(request, "No puede desactivarse a sí mismo", "danger")
        return RedirectResponse(f"/users/{uid}", 303)
    db.add(u)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        flash(request, "Nombre de usuario duplicado", "danger")
        return RedirectResponse(f"/users/{uid}" if uid else "/users/new", 303)
    audit(db, request, user, "user_save", "user", u.id, u.username)
    db.commit()
    flash(request, "Usuario guardado")
    return RedirectResponse(f"/users/{u.id}", 303)


@router.get("/roles")
def roles_list(request: Request, user: User = Depends(require("users.manage")), db: Session = Depends(get_db)):
    return render(request, "roles.html", roles=db.query(Role).order_by(Role.name).all())


@router.post("/roles/save")
def role_save(request: Request, form: dict = Depends(checked_form), user: User = Depends(require("users.manage")),
              db: Session = Depends(get_db)):
    rid = form.get("id")
    r = db.get(Role, int(rid)) if rid else Role()
    if r is None:
        raise HTTPException(404)
    if form.get("delete") and r.id:
        if r.users:
            flash(request, "No se puede borrar un perfil con usuarios asignados", "danger")
        else:
            db.delete(r)
            db.commit()
        return RedirectResponse("/roles", 303)
    r.name = s(form.get("name")) or "Perfil"
    r.description = s(form.get("description"))
    perms = form.get("perms[]", [])
    if isinstance(perms, str):
        perms = [perms]
    r.permissions = ["*"] if "*" in perms else [p for p in perms if p in PERMISSIONS]
    db.add(r)
    try:
        audit(db, request, user, "role_save", "role", r.id, ",".join(r.permissions))
        db.commit()
    except IntegrityError:
        db.rollback()
        flash(request, "Nombre de perfil duplicado", "danger")
    return RedirectResponse("/roles", 303)


# ------------------------------------------------------------------ configuración
INT_SETTINGS = {"group_window_minutes", "dc09_timestamp_tolerance", "smtp_port", "events_retention_days"}
BOOL_SETTINGS = {"dc09_reject_unknown", "sound_alerts"}


@router.get("/settings")
def settings_page(request: Request, user: User = Depends(require("settings.manage"))):
    from ..config import config
    return render(request, "settings.html", st=settings_store.all_settings(), cfg=config)


@router.post("/settings")
def settings_save(request: Request, form: dict = Depends(checked_form), user: User = Depends(require("settings.manage")),
                  db: Session = Depends(get_db)):
    values = {}
    for key in settings_store.DEFAULTS:
        if key == "alarm_categories":
            cats = form.get("alarm_categories[]", [])
            values[key] = [cats] if isinstance(cats, str) else cats
        elif key in BOOL_SETTINGS:
            values[key] = b(form.get(key))
        elif key in INT_SETTINGS:
            values[key] = int(form.get(key) or 0)
        elif key == "smtp_password" and not form.get(key):
            continue  # vacío = no cambiar
        elif key in form:
            values[key] = s(form.get(key))
    if values.get("dc09_default_key"):
        from ..protocols.dc09 import parse_key
        try:
            parse_key(values["dc09_default_key"], values.get("dc09_default_key_format", "hex"))
        except ValueError as exc:
            flash(request, str(exc), "danger")
            return RedirectResponse("/settings", 303)
    settings_store.set_many(db, values)
    audit(db, request, user, "settings_save", "settings",
          details=json.dumps({k: v for k, v in values.items() if "password" not in k and "key" not in k}))
    db.commit()
    flash(request, "Configuración guardada")
    return RedirectResponse("/settings", 303)


@router.post("/settings/test-email")
async def settings_test_email(request: Request, form: dict = Depends(checked_form),
                              user: User = Depends(require("settings.manage"))):
    to = s(form.get("to")) or user.email
    try:
        await notify.send_email([to], "Prueba AlarmHub", "Correo de prueba enviado desde AlarmHub.")
        flash(request, f"Correo enviado a {to}")
    except Exception as exc:
        flash(request, f"Error enviando correo: {exc}", "danger")
    return RedirectResponse("/settings", 303)


@router.get("/audit")
def audit_page(request: Request, page: int = 1, user: User = Depends(require("audit.view")),
               db: Session = Depends(get_db)):
    q = db.query(AuditLog).order_by(AuditLog.id.desc())
    return render(request, "audit.html", items=q.offset((page - 1) * 100).limit(100).all(), page=page,
                  total=q.count())


@router.get("/notifications")
def notifications_page(request: Request, page: int = 1, user: User = Depends(require("audit.view")),
                       db: Session = Depends(get_db)):
    q = db.query(NotificationLog).order_by(NotificationLog.id.desc())
    return render(request, "notifications.html", items=q.offset((page - 1) * 100).limit(100).all(), page=page,
                  total=q.count())
