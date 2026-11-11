# AlarmSim – Generador de alarmas SIA DC-09

Herramienta web en Python para **generar y enviar alarmas SIA DC-09** a receptoras y software de
CRA: pruebas de integración, puesta en marcha, validación de receptoras, formación y pruebas de
carga. Admite cifrado AES opcional y todos los campos que admite DC-09: zonas, particiones,
usuarios, texto, URL de verificación, coordenadas GPS, etc.

```
 Navegador / API / consola                 AlarmSim                                 Receptora CRA
┌───────────────────────────┐   ┌─────────────────────────────────────┐   ┌──────────────────────────┐
│ Generador (vista previa)  │──▶│ Constructor DC-09 ─▶ AES (opcional) │──▶│ TCP / UDP                │
│ Escenarios · Carga        │   │  CRC · longitud · secuencia · hora  │◀──│ ACK / NAK / DUH          │
│ Envíos periódicos (NULL)  │   │ Histórico · Reintentos · Latencia   │   └──────────────────────────┘
└───────────────────────────┘   │ Receptor de pruebas integrado ◀─────┼── (para probar sin receptora)
                                └─────────────────────────────────────┘
```

## Instalación

### Linux con instalador automático (Debian/Ubuntu/RHEL/Rocky/Fedora)

```bash
sudo ./install.sh
```

El instalador instala Python, crea el usuario `alarmsim`, copia la aplicación a `/opt/alarmsim`,
crea el entorno virtual, genera `alarmsim.env` con clave secreta, crea el usuario `admin` con una
contraseña aleatoria (se muestra al final), crea el servicio `systemd` y abre los puertos en
`ufw`/`firewalld`. Opciones: `--web-port 8080 --receiver-port 7100 --admin-password '…' --dir …
--no-systemd --no-firewall --uninstall`. Volver a ejecutarlo actualiza la aplicación sin tocar los datos.

### Docker

```bash
cp alarmsim.env.example alarmsim.env && docker compose up -d --build
```

### Manual

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python -m alarmsim run
```

Sin instalador, el primer acceso es `admin` / `admin` y se obliga a cambiar la contraseña.
Viene con una receptora, un panel de demostración (7 zonas, 2 particiones y 2 usuarios) y escenarios
de ejemplo que apuntan al receptor de pruebas integrado: se puede enviar la primera alarma al
momento.

## Qué se puede generar

### Protocolo
| Elemento | Opciones |
|---|---|
| Tipo de mensaje | `SIA-DCS` (SIA DC-04), `ADM-CID` (Contact ID), `NULL` (prueba de enlace) |
| Cabecera | Nº de abonado `#acct` (3-16 hex), receptor `R` (o se omite), prefijo de línea `L`, secuencia 0001-9999 automática por panel o manual |
| Cifrado | AES-128/192/256 CBC; clave por panel o por receptora, en hexadecimal o ASCII; modo automático, siempre o nunca |
| Marca de tiempo | `_HH:MM:SS,MM-DD-YYYY` en UTC; se puede quitar (si no se cifra) o desplazar N segundos |
| SIA-DCS | Código de 2 letras con dirección (zona o usuario), partición `ri`, usuario `id`, hora `ti`, **varios eventos en un mismo mensaje**, texto en línea `^…^` |
| ADM-CID | Calificador E/R/P (1/3/6), código de evento de 3 dígitos, grupo/partición (2 dígitos) y zona/usuario (3 dígitos) |
| Pruebas de robustez | CRC incorrecto, longitud incorrecta, clave errónea, hora desfasada, sin `#abonado` en el bloque de datos |

### Datos extendidos DC-09 (bloques `[…]`)
| | Campo | | Campo |
|---|---|---|---|
| `I` | Texto de alarma | `X` / `Y` / `Z` | Longitud / latitud / altitud GPS |
| `V` | Verificación: **URL** de vídeo, audio o imagen | `H` | Hora real del suceso |
| `L` | Ubicación en la instalación | `M` | Dirección MAC |
| `R` | Sala / estancia | `N` | Dirección de red |
| `O` | Edificio | `J` / `T` | Vía de red / disparo de alarma |
| `S` | Nombre del sitio | `C` | Categoría de supervisión |
| `A` / `P` | Hash de autenticación | `K` | Clave de cifrado |

Los corchetes dentro de un valor se sustituyen por paréntesis para no romper la trama. Con
«Completar automáticamente», el nombre de la zona se envía en `I`, la sala en `R` y se añaden los
datos fijos del panel (sitio, edificio, GPS, MAC…).

### Paneles, zonas, particiones y usuarios
Cada panel simulado tiene su abonado, receptora destino, protocolo, clave, secuencia y datos fijos.
Sus **zonas** (con nombre, partición, código SIA y Contact ID por defecto y sala), **particiones** y
**usuarios** aparecen en el generador como listas de selección y como botones de envío rápido
(alarma, restauración y sabotaje de cada zona; armar, desarmar, pánico, incendio, fallo de 230 V,
test, NULL…). Las zonas se pueden dar de alta en bloque (`1-16,20`).

## Funciones

* **Generador** con vista previa en vivo (contenido en claro, trama transmitida y hexadecimal),
  respuesta de la receptora descifrada y latencia; repeticiones con intervalo y plantillas
  reutilizables. `Ctrl+Enter` envía.
* **Escenarios**: secuencias de alarmas y pausas (fijas o aleatorias), en bucle o un número de
  veces, con progreso en tiempo real. Ejemplo:
  ```json
  [{"type": "event", "spec": {"code": "BA", "zone": "1", "partition": "1", "xdata": {"I": "Primera detección"}}},
   {"type": "wait", "seconds": 5},
   {"type": "event", "spec": {"code": "BA", "zone": "2", "xdata": {"V": "https://video.example.com/clip/1"}}},
   {"type": "event", "spec": {"token": "ADM-CID", "qualifier": "R", "code": "401", "zone": "1", "partition": "1"}},
   {"type": "null"}]
  ```
* **Envíos periódicos** por panel: prueba de enlace NULL y test periódico (RP / 602) cada N segundos.
* **Prueba de carga**: N paneles × M mensajes con concurrencia configurable; muestra mensajes/s,
  ACK, NAK, *timeouts* y latencia media, p95 y máxima.
* **Receptoras**: TCP o UDP, *timeout*, reintentos tras NAK o *timeout*, conexión TCP persistente.
* **Trama manual**: se escribe el cuerpo de la trama y el CRC y la longitud se calculan solos.
* **Receptor de pruebas integrado** (TCP+UDP, puerto 7100 por defecto): decodifica y muestra cada
  mensaje (eventos, datos extendidos, desfase horario) y responde ACK, NAK, DUH o nada.
* **Histórico** de todos los envíos con trama, respuesta, resultado y latencia; exportación a CSV.
* **API REST** (`/api/docs`) con API key y **consola**.

## Consola

```bash
python -m alarmsim send --host 10.0.0.5 --port 7000 --account 1234 --code BA --zone 3 --partition 1 --text "Puerta principal" --url https://cam.example/clip/1 --lat N40.4168 --lon W003.7038 --key 000102030405060708090A0B0C0D0E0F
```

```bash
python -m alarmsim send --port 7000 --udp --account 1234 --cid E --code 130 --partition 1 --zone 5 --count 10 --interval 2
```

```bash
python -m alarmsim send --account 1234 --null --dry-run
```

Otros campos con `-x LETRA=valor` (por ejemplo `-x H=10:00:00,11-11-2026`). Opciones: `--receiver`,
`--line`, `--user`, `--ti`, `--inline-text`, `--no-timestamp`, `--ts-offset`, `--seq`, `--retries`.
Con `--dry-run` solo se muestra la trama.

## API

```bash
curl -X POST http://servidor:8080/api/send -H "X-API-Key: as_..." -H "Content-Type: application/json" -d '{"panel_id": 1, "spec": {"code": "BA", "zone": "3", "partition": "1", "xdata": {"I": "Puerta", "V": "https://cam/1"}}}'
```

El `spec` es el mismo que usan los escenarios: `token, code, qualifier, zone, partition, user,
extra_events, time_ti, inline_text, xdata, auto_xdata, options{encryption, timestamp,
timestamp_offset, corrupt_crc, corrupt_length, account_in_data, sequence}`. Otros endpoints:
`/api/preview`, `/api/raw`, `/api/scenarios/{id}/run`, `/api/load`, `/api/jobs`, `/api/panels`, `/api/codes`.
La API key se genera en *Mi cuenta*.

## Estructura

```
alarmsim/
  protocol/  dc09.py (tramas, CRC, AES, respuestas), xdata.py (campos extendidos), codes.py (SIA / Contact ID)
  core/      builder.py (spec → trama), sender.py (TCP/UDP, reintentos), service.py, runner.py
             (escenarios, carga, periódicos), receiver.py (receptor de pruebas)
  web/       páginas y API        templates/ static/   interfaz
tests/       protocolo, envío real al receptor de pruebas, web y API
install.sh   instalador automático
```

```bash
pip install pytest && python -m pytest -q tests
```
