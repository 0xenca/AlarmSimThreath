# AlarmHub – Receptora y plataforma de gestión de alarmas

Plataforma web en Python para centrales receptoras de alarmas (CRA) y empresas de seguridad:
recibe señales de paneles de intrusión por **SIA DC-09** (TCP/UDP, cifrado AES opcional) y otros
protocolos, las asocia a clientes y paneles, genera alarmas para los operadores y permite
**automatizar** su tratamiento con workflows (buffer, verificación por segunda señal, emails,
webhooks, Telegram…).

```
 Paneles / comunicadores                 AlarmHub                                  Usuarios
 ┌─────────────────────┐   ┌──────────────────────────────────────────────┐   ┌──────────────┐
 │ SIA DC-09 TCP/UDP   │──▶│ Receptores ─▶ Cola ─▶ Procesador ─▶ Alarmas  │──▶│ Web (tiempo  │
 │  (SIA-DCS, ADM-CID, │   │   ACK/NAK      ordenada   │  │       (cola     │   │ real + sonido│
 │   NULL, AES-128/256)│   │                           │  └─▶ Workflows ───┼──▶│ API REST     │
 │ Sur-Gard MLR2 (CID) │──▶│                           │      (buffer,     │   │ Email/Webhook│
 │ HTTP/JSON (IoT, IP) │──▶│                           ▼       esperas…)   │   │ Telegram     │
 └─────────────────────┘   │                    SQLite / PostgreSQL        │   └──────────────┘
                           └──────────────────────────────────────────────┘
```

## Instalación rápida

### Linux con instalador automático (Debian/Ubuntu/RHEL/Rocky/Fedora)

```bash
git clone <este-repositorio> alarmhub
```

```bash
cd alarmhub && sudo ./install.sh
```

El instalador:

1. instala Python 3 y sus dependencias del sistema,
2. crea el usuario de servicio `alarmhub` y copia la aplicación a `/opt/alarmhub`,
3. crea un entorno virtual e instala las librerías,
4. genera `alarmhub.env` con una clave secreta aleatoria,
5. inicializa la base de datos y crea el usuario `admin` con una contraseña aleatoria (se muestra al final),
6. crea y arranca el servicio `systemd` `alarmhub` (con reinicio automático),
7. abre los puertos en `ufw`/`firewalld` si están activos.

Opciones: `--web-port 8080 --dc09-port 7000 --surgard-port 0 --admin-password '...' --dir /opt/alarmhub
--no-systemd --no-firewall --uninstall`. Volver a ejecutarlo **actualiza** el código conservando
configuración y datos.

```bash
journalctl -u alarmhub -f
```

### Docker

```bash
cp alarmhub.env.example alarmhub.env && docker compose up -d --build
```

```bash
docker compose exec alarmhub python -m alarmhub create-admin admin
```

### Manual / desarrollo

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
cp alarmhub.env.example alarmhub.env && .venv/bin/python -m alarmhub run
```

Sin instalador, el primer acceso es `admin` / `admin` y se obliga a cambiar la contraseña.

## Funcionalidades

### Recepción
| Protocolo | Transporte | Detalles |
|---|---|---|
| **SIA DC-09** (`SIA-DCS`, `ADM-CID`, `NULL`) | TCP y UDP (mismo puerto) | CRC-16, longitud, ACK / NAK / DUH, número de secuencia, receptor `R`, prefijo `L`, bloques extendidos `[X][Y][H]…`, varios eventos por mensaje |
| **Cifrado DC-09** | AES-128/192/256 CBC | Clave por panel (hex o ASCII) o clave por defecto; opción de *exigir* cifrado; validación de la marca de tiempo (anti-replay) con tolerancia configurable |
| **Sur-Gard MLR2** | TCP | Contact ID `5RRL 18AAAAQEEEGGZZZ`, latidos, ACK `0x06` (para integrar receptoras físicas o software que reenvía) |
| **HTTP/JSON** | `POST /api/v1/ingest` | Para cámaras, dispositivos IoT, apps de pánico u otras plataformas. Acepta códigos SIA (`BA`), Contact ID (`E130`, `R401`) o propios |

* El ACK sólo se envía **después** de que la señal está guardada en la base de datos.
* Las señales de abonados no dados de alta se guardan igualmente (vista *Desconocidos*); al dar de alta
  el panel se vinculan automáticamente. Opcionalmente se responde NAK.
* **Supervisión**: cada panel puede tener un tiempo máximo sin comunicar; al superarlo se genera
  el evento interno `SUPV` (alarma) y `SUPR` cuando vuelve a comunicar.
* **Modo prueba** por panel (30 min, 1 h, 24 h…): las señales se registran pero no generan alarmas.
* Normalización de todos los protocolos a categorías comunes: incendio, pánico, médica, intrusión,
  sabotaje, avería, supervisión, restauración, apertura, cierre, anulación, test, informativo.

### Gestión de alarmas (operadores)
* Cola en **tiempo real** (Server-Sent Events) ordenada por prioridad, con aviso emergente y **sonoro**.
* Agrupación de señales del mismo panel en la alarma abierta; las restauraciones se marcan.
* Atender, asignar, cambiar prioridad, comentar (nota / llamada), cerrar con resolución tipificada,
  reabrir, cierre masivo.
* Ficha con datos del cliente, **palabra clave y de coacción**, instrucciones al operador, lista de
  contactos por orden de llamada, enlace a mapa, historial del panel y bitácora completa.

### Clientes y paneles
* Clientes con datos fiscales, dirección, grupo (delegación, instalador, zona comercial…),
  contactos, notas y comentarios. Importación masiva por **CSV**.
* Un cliente puede tener varios paneles; cada panel tiene abonado, prefijo, fabricante/modelo,
  clave AES, supervisión, geolocalización, **zonas** y **usuarios** con nombre (aparecen en avisos).
* Estado armado/desarmado según las aperturas/cierres recibidos.

### Histórico
* Búsqueda por abonado, cliente, código, categoría, protocolo, fechas; exportación CSV.
* Detalle de cada señal con la trama original, origen IP, cifrado y comentarios.
* Registro de **auditoría** de acciones de usuarios y registro de **notificaciones** enviadas.

### Usuarios, perfiles y permisos
Perfiles editables con permisos granulares (`alarms.view`, `alarms.handle`, `events.export`,
`clients.edit`, `clients.secrets`, `workflows.edit`, `users.manage`, `settings.manage`,
`audit.view`, `api.ingest`…). Perfiles por defecto: Administrador, Supervisor, Operador, Técnico,
Solo lectura, Integración API. Cada usuario puede restringirse a **grupos de clientes**
(multi-delegación / multi-instalador) y tener su propia **API key**.

## Workflows (automatización)

Cada señal se compara con los workflows activos (por orden). Un workflow es un JSON:

```json
{
  "trigger": {"categories": ["alarm"], "client_groups": ["norte"],
              "schedule": {"days": [0,1,2,3,4], "from": "20:00", "to": "08:00"}},
  "hold_alarm": true,
  "steps": [
    {"type": "wait_for", "categories": ["alarm"], "different_zone": true, "timeout": 120,
     "on_match":   [{"type": "set_priority", "value": 1},
                    {"type": "release_alarm", "comment": "Confirmada zona {zone} + {matched_zone}"},
                    {"type": "email", "to": "@contacts", "subject": "Alarma en {client_name}"}],
     "on_timeout": [{"type": "close_alarm", "resolution": "auto", "comment": "No confirmada"}]}
  ]
}
```

**Condiciones del trigger** (todas deben cumplirse; dentro de cada lista basta una):
`categories`, `codes` / `exclude_codes` (con comodines `B*`, `E1*`), `accounts`, `client_ids`,
`client_groups`, `zones`, `partitions`, `protocols`, `armed_state`, `max_priority`,
`unknown_account`, `schedule` / `not_schedule`, `text_regex`, `fields` (comparaciones libres
sobre cualquier variable), `exclude_test`.

**Pasos**:

| Paso | Qué hace |
|---|---|
| `wait_for` | Espera otra señal del mismo panel (por código/categoría, misma o distinta zona/partición) durante N s → `on_match` / `on_timeout` |
| `count` | Cuenta señales en una ventana de tiempo → `then` / `else` (ráfagas, zona averiada…) |
| `condition` | Ramas con las mismas condiciones que el trigger (horarios, zonas, estado…) |
| `delay` | Espera N segundos |
| `hold_alarm` / `release_alarm` | **Buffer**: oculta la alarma a los operadores / la libera |
| `close_alarm`, `create_alarm`, `set_priority`, `assign`, `tag`, `comment` | Gestión de la alarma |
| `email`, `webhook`, `telegram`, `notify` | Notificaciones (destinos especiales `@contacts`, `@client`) |
| `stop` | Termina el workflow |

Plantillas con variables: `{account} {client_name} {client_address} {zone} {zone_name} {user_name}
{description} {category_name} {priority} {received_at} {alarm_id} {matched_zone} {count}…`

**Seguridad (fail-safe)**: una alarma retenida en buffer nunca se pierde. Si el workflow acaba sin
decidir, falla, se cancela o el servicio se reinicia, la alarma se libera a la cola del operador.
Un workflow sólo cierra alarmas que él mismo originó o retiene (salvo `"force": true`) y nunca las
que un operador tiene en gestión.

Incluye 5 workflows de ejemplo (desactivados): verificación por doble zona, corte de red con espera
de restauración, notificación inmediata de incendio/pánico, ráfagas en la misma zona y apertura
fuera de horario. El editor web permite probar el trigger con un evento de ejemplo.

## API REST

Documentación interactiva en `/api/docs`. Autenticación con cabecera `X-API-Key` (generada en la
ficha del usuario; los permisos dependen de su perfil).

```bash
curl -X POST http://servidor:8080/api/v1/ingest -H "X-API-Key: ah_..." -H "Content-Type: application/json" -d '{"account": "1234", "code": "PA", "zone": "2", "text": "Botón de pánico app"}'
```

Endpoints: `POST /ingest`, `GET /alarms`, `GET /alarms/{id}`, `POST /alarms/{id}/close`,
`GET /events?since_id=`, `GET /clients`, `GET|POST /panels`, `GET /status`.

## Simulador de paneles

`tools/dc09_sim.py` envía tramas DC-09 reales para pruebas y puesta en marcha:

```bash
python tools/dc09_sim.py --account 1234 --sia "Nri1/BA3"
```

```bash
python tools/dc09_sim.py --account 1234 --cid "1130 01 003" --key 000102030405060708090A0B0C0D0E0F --udp
```

```bash
python tools/dc09_sim.py --account 1234 --scenario double-knock
```

```bash
python tools/dc09_sim.py --load 50 --repeat 20
```

Escenarios: `double-knock`, `single-knock`, `fire`, `ac-fail-restore`, `open-close`, `panic`.

## Configuración

* `alarmhub.env` (requiere reinicio): base de datos, puertos, clave de sesión. Ver `alarmhub.env.example`.
* Web → *Configuración*: SMTP, categorías que generan alarma, ventana de agrupación, tolerancia de
  hora DC-09, clave AES por defecto, NAK a desconocidos, retención de señales, sonido.

Para gran volumen se recomienda PostgreSQL (`pip install "psycopg[binary]"` y
`DATABASE_URL=postgresql+psycopg://usuario:clave@host/alarmhub`) y publicar la web tras un proxy
inverso con HTTPS (`SECURE_COOKIES=true`).

## Estructura

```
alarmhub/
  protocols/   dc09.py (tramas, CRC, AES), surgard.py, codes.py (tablas SIA / Contact ID), normalized.py
  receivers/   dc09_server.py (TCP+UDP), surgard_server.py
  engine/      processor.py (cola, persistencia, alarmas, supervisión), workflows.py, notify.py, context.py
  web/         rutas de operación, clientes, administración y API REST
  templates/   interfaz (Jinja2)        static/  CSS + JS (tiempo real, sonido)
tools/dc09_sim.py    simulador de paneles
tests/               pruebas (protocolo, receptor real, workflows, web)
install.sh           instalador automático
```

## Tests

```bash
pip install pytest && python -m pytest -q tests
```
