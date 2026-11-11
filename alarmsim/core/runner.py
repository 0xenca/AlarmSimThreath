"""Tareas en segundo plano: escenarios, envíos periódicos (NULL / test) y pruebas de carga."""
from __future__ import annotations

import asyncio
import logging
import random
import statistics
import time
from dataclasses import dataclass, field
from typing import Any

from ..db import session_scope
from ..models import Panel, Scenario, Target
from ..protocol import dc09
from .bus import bus
from .sender import send
from .service import send_spec, trim_history

log = logging.getLogger("alarmsim.runner")


@dataclass
class Job:
    id: str
    kind: str                       # scenario / load
    name: str
    task: asyncio.Task | None = None
    status: str = "running"
    log: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    started: float = field(default_factory=time.time)

    def add(self, line: str) -> None:
        self.log.append(time.strftime("%H:%M:%S ") + line)
        self.log = self.log[-500:]
        bus.publish("job", {"job": self.as_dict(short=True), "line": self.log[-1]})

    def as_dict(self, short: bool = False) -> dict[str, Any]:
        d = {"id": self.id, "kind": self.kind, "name": self.name, "status": self.status, "stats": self.stats,
             "elapsed": round(time.time() - self.started, 1)}
        if not short:
            d["log"] = self.log
        return d


class Runner:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self._counter = 0
        self._schedule_task: asyncio.Task | None = None
        self._last_run: dict[tuple[int, str], float] = {}

    def _new_job(self, kind: str, name: str) -> Job:
        self._counter += 1
        job = Job(id=f"{kind[0]}{self._counter}", kind=kind, name=name)
        self.jobs[job.id] = job
        # conservar sólo los 50 trabajos más recientes
        for old in list(self.jobs)[:-50]:
            if self.jobs[old].status != "running":
                del self.jobs[old]
        return job

    def stop(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job and job.task and not job.task.done():
            job.task.cancel()
            return True
        return False

    # ------------------------------------------------------------ escenarios
    def start_scenario(self, scenario_id: int, panel_id: int | None = None) -> Job:
        with session_scope() as db:
            sc = db.get(Scenario, scenario_id)
            if sc is None:
                raise ValueError("Escenario no encontrado")
            pid = panel_id or sc.panel_id
            if not pid:
                raise ValueError("El escenario no tiene panel asignado")
            steps, repeat, delay, name = list(sc.steps or []), sc.repeat, sc.repeat_delay, sc.name
        job = self._new_job("scenario", name)
        job.task = asyncio.create_task(self._run_scenario(job, pid, steps, repeat, delay))
        return job

    async def _run_scenario(self, job: Job, panel_id: int, steps: list[dict], repeat: int, delay: float) -> None:
        job.stats = {"sent": 0, "ack": 0, "failed": 0, "loop": 0}
        try:
            n = 0
            while repeat == 0 or n < repeat:
                n += 1
                job.stats["loop"] = n
                job.add(f"— Iteración {n}{'' if repeat == 0 else f'/{repeat}'}")
                for i, step in enumerate(steps, 1):
                    kind = step.get("type", "event")
                    if kind == "wait":
                        secs = float(step.get("seconds", 1))
                        if step.get("random_to"):
                            secs = random.uniform(secs, float(step["random_to"]))
                        job.add(f"Paso {i}: espera {secs:.1f} s")
                        await asyncio.sleep(secs)
                        continue
                    spec = dict(step.get("spec") or {})
                    if kind == "null":
                        spec = {"token": "NULL"}
                    pid = int(step.get("panel_id") or panel_id)
                    try:
                        res = await send_spec(pid, spec, source="escenario")
                    except Exception as exc:  # error de configuración
                        job.stats["failed"] += 1
                        job.add(f"Paso {i}: ERROR {exc}")
                        if step.get("stop_on_fail"):
                            raise asyncio.CancelledError from exc
                        continue
                    job.stats["sent"] += 1
                    job.stats["ack" if res["result"] == "ACK" else "failed"] += 1
                    job.add(f"Paso {i}: {res['summary']} → {res['result']}"
                            + (f" ({res['latency_ms']} ms)" if res["latency_ms"] is not None else "")
                            + (f" {res['error']}" if res["error"] else ""))
                    if res["result"] != "ACK" and step.get("stop_on_fail"):
                        job.add("Detenido: el paso exige ACK")
                        job.status = "failed"
                        return
                if repeat == 0 or n < repeat:
                    await asyncio.sleep(delay)
            job.status = "done"
            job.add("Escenario terminado")
        except asyncio.CancelledError:
            job.status = "stopped"
            job.add("Detenido por el usuario")
        finally:
            trim_history()
            bus.publish("job", {"job": job.as_dict(short=True)})

    # ------------------------------------------------------------ carga
    def start_load(self, params: dict[str, Any]) -> Job:
        job = self._new_job("load", f"Carga {params.get('panels')}×{params.get('messages')}")
        job.task = asyncio.create_task(self._run_load(job, params))
        return job

    async def _run_load(self, job: Job, p: dict[str, Any]) -> None:
        with session_scope() as db:
            target = db.get(Target, int(p["target_id"]))
            if target is None:
                job.status = "failed"
                job.add("Destino no encontrado")
                return
        key = dc09.parse_key(p.get("key") or target.enc_key, target.key_format) if (p.get("key") or target.enc_key) \
            and p.get("encrypt", True) else None
        panels, messages = int(p.get("panels", 10)), int(p.get("messages", 10))
        concurrency = max(1, int(p.get("concurrency", 50)))
        interval = float(p.get("interval", 0))
        start_acc = int(str(p.get("account_start", "9000")), 16)
        codes = [c.strip().upper() for c in str(p.get("codes") or "BA,BR,OP,CL,RP,TA,AT,AR,FA,FR").split(",") if c.strip()]
        lat: list[float] = []
        st = job.stats = {"total": panels * messages, "sent": 0, "ack": 0, "nak": 0, "timeout": 0, "error": 0}
        sem = asyncio.Semaphore(concurrency)
        t0 = time.perf_counter()
        job.add(f"Inicio: {panels} paneles × {messages} mensajes → {target.label}{' cifrado' if key else ''}")

        async def panel_worker(i: int) -> None:
            acct = f"{start_acc + i:X}".zfill(4)
            for n in range(messages):
                code = random.choice(codes)
                data = dc09.sia_dcs_data([dc09.SIAEvent(code, str(random.randint(1, 32)), "1")])
                m = dc09.build_message("SIA-DCS", acct, data, None, dc09.BuildOptions(
                    sequence=n + 1, receiver=target.receiver, line=target.line or "0", key=key,
                    timestamp=target.include_timestamp))
                async with sem:
                    r = await send(target.host, target.port, target.transport, m.frame, key, m.sequence,
                                   target.timeout, 0, False)
                st["sent"] += 1
                k = {"ACK": "ack", "NAK": "nak", "TIMEOUT": "timeout"}.get(r.result, "error")
                st[k] += 1
                if r.latency_ms is not None:
                    lat.append(r.latency_ms)
                if interval:
                    await asyncio.sleep(interval)

        async def progress() -> None:
            while True:
                await asyncio.sleep(1)
                el = time.perf_counter() - t0
                st["rate"] = round(st["sent"] / el, 1) if el else 0
                bus.publish("job", {"job": job.as_dict(short=True)})

        prog = asyncio.create_task(progress())
        try:
            await asyncio.gather(*(panel_worker(i) for i in range(panels)))
            job.status = "done"
        except asyncio.CancelledError:
            job.status = "stopped"
        finally:
            prog.cancel()
            el = time.perf_counter() - t0
            st["rate"] = round(st["sent"] / el, 1) if el else 0
            st["seconds"] = round(el, 2)
            if lat:
                lat.sort()
                st["lat_avg"] = round(statistics.mean(lat), 1)
                st["lat_p95"] = round(lat[int(len(lat) * 0.95) - 1 if len(lat) > 1 else 0], 1)
                st["lat_max"] = round(lat[-1], 1)
            job.add(f"Fin: {st}")

    # ------------------------------------------------------------ envíos periódicos
    def start_scheduler(self) -> None:
        self._schedule_task = asyncio.create_task(self._scheduler())

    async def shutdown(self) -> None:
        if self._schedule_task:
            self._schedule_task.cancel()
        for job in self.jobs.values():
            if job.task and not job.task.done():
                job.task.cancel()

    async def _scheduler(self) -> None:
        while True:
            try:
                now = time.time()
                due: list[tuple[int, dict[str, Any], str]] = []
                with session_scope() as db:
                    for p in db.query(Panel).filter(Panel.schedules_enabled.is_(True)):
                        if p.null_interval > 0 and now - self._last_run.get((p.id, "null"), 0) >= p.null_interval:
                            self._last_run[(p.id, "null")] = now
                            due.append((p.id, {"token": "NULL"}, "null"))
                        if p.test_interval > 0 and now - self._last_run.get((p.id, "test"), 0) >= p.test_interval:
                            self._last_run[(p.id, "test")] = now
                            spec = {"code": "RP"} if p.protocol == "SIA-DCS" else \
                                {"token": "ADM-CID", "code": "602", "qualifier": "E", "zone": "0", "partition": "0"}
                            due.append((p.id, spec, "test"))
                for pid, spec, source in due:
                    asyncio.create_task(self._safe_send(pid, spec, source))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Error en el planificador")
            await asyncio.sleep(1)

    async def _safe_send(self, pid: int, spec: dict[str, Any], source: str) -> None:
        try:
            await send_spec(pid, spec, source=source)
        except Exception as exc:
            log.warning("Envío periódico fallido (panel %s): %s", pid, exc)


runner = Runner()
