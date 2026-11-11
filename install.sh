#!/usr/bin/env bash
# Instalador automático de AlarmHub (Debian/Ubuntu, RHEL/Rocky/Alma, Fedora).
#
#   sudo ./install.sh                     instalación interactiva con valores por defecto
#   sudo ./install.sh --web-port 80 --dc09-port 7000 --admin-password 'Secreta123'
#   sudo ./install.sh --uninstall         elimina servicio y usuario (conserva los datos)
#
# Repetir la instalación actualiza el código y las dependencias sin tocar la
# configuración (alarmhub.env) ni la base de datos (data/).
set -euo pipefail

APP_DIR=/opt/alarmhub
APP_USER=alarmhub
WEB_PORT=8080
DC09_PORT=7000
SURGARD_PORT=0
ADMIN_PASSWORD=""
SYSTEMD=1
FIREWALL=1
UNINSTALL=0
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() { sed -n '2,10p' "$0"; cat <<USAGE
Opciones:
  --dir DIR               Directorio de instalación (por defecto $APP_DIR)
  --user USUARIO          Usuario del servicio (por defecto $APP_USER)
  --web-port N            Puerto de la web (por defecto $WEB_PORT)
  --dc09-port N           Puerto TCP/UDP del receptor SIA DC-09 (por defecto $DC09_PORT)
  --surgard-port N        Puerto receptor Sur-Gard MLR2 (0 = desactivado)
  --admin-password PASS   Contraseña del usuario admin (si no, se genera)
  --no-systemd            No crear servicio systemd
  --no-firewall           No abrir puertos en ufw/firewalld
  --uninstall             Desinstalar
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) APP_DIR="$2"; shift 2;;
    --user) APP_USER="$2"; shift 2;;
    --web-port) WEB_PORT="$2"; shift 2;;
    --dc09-port) DC09_PORT="$2"; shift 2;;
    --surgard-port) SURGARD_PORT="$2"; shift 2;;
    --admin-password) ADMIN_PASSWORD="$2"; shift 2;;
    --no-systemd) SYSTEMD=0; shift;;
    --no-firewall) FIREWALL=0; shift;;
    --uninstall) UNINSTALL=1; shift;;
    -h|--help) usage; exit 0;;
    *) echo "Opción desconocida: $1"; usage; exit 1;;
  esac
done

run_as() {
  if command -v runuser >/dev/null; then runuser -u "$APP_USER" -- "$@"; else sudo -u "$APP_USER" "$@"; fi
}
say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Ejecute como root (sudo ./install.sh)"

if [[ $UNINSTALL -eq 1 ]]; then
  say "Deteniendo y eliminando el servicio"
  systemctl disable --now alarmhub 2>/dev/null || true
  rm -f /etc/systemd/system/alarmhub.service
  systemctl daemon-reload 2>/dev/null || true
  say "Los datos se conservan en $APP_DIR (bórrelo manualmente si lo desea)"
  exit 0
fi

# ---------------------------------------------------------------- dependencias
say "Instalando dependencias del sistema"
if command -v apt-get >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq python3 python3-venv python3-pip rsync ca-certificates >/dev/null
elif command -v dnf >/dev/null; then
  dnf install -y -q python3 python3-pip rsync ca-certificates >/dev/null
elif command -v yum >/dev/null; then
  yum install -y -q python3 python3-pip rsync ca-certificates >/dev/null
else
  say "Gestor de paquetes no reconocido: se asume Python 3.10+ ya instalado"
fi

PY=$(command -v python3) || die "python3 no encontrado"
"$PY" - <<'PYCHK' || die "Se necesita Python 3.10 o superior"
import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)
PYCHK

# ---------------------------------------------------------------- usuario y ficheros
if ! id "$APP_USER" >/dev/null 2>&1; then
  say "Creando usuario de sistema $APP_USER"
  useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi

say "Copiando aplicación a $APP_DIR"
mkdir -p "$APP_DIR/data"
if [[ "$SRC_DIR" != "$APP_DIR" ]]; then
  rsync -a --delete --exclude '.git' --exclude 'data' --exclude 'alarmhub.env' --exclude '.venv' \
        --exclude '__pycache__' "$SRC_DIR/" "$APP_DIR/"
fi

say "Creando entorno virtual e instalando librerías Python"
[[ -d "$APP_DIR/.venv" ]] || "$PY" -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

# ---------------------------------------------------------------- configuración
ENV_FILE="$APP_DIR/alarmhub.env"
if [[ ! -f "$ENV_FILE" ]]; then
  say "Generando $ENV_FILE"
  SECRET=$("$PY" -c 'import secrets; print(secrets.token_urlsafe(48))')
  sed -e "s|^SECRET_KEY=.*|SECRET_KEY=$SECRET|" \
      -e "s|^WEB_PORT=.*|WEB_PORT=$WEB_PORT|" \
      -e "s|^DC09_TCP_PORT=.*|DC09_TCP_PORT=$DC09_PORT|" \
      -e "s|^DC09_UDP_PORT=.*|DC09_UDP_PORT=$DC09_PORT|" \
      -e "s|^SURGARD_PORT=.*|SURGARD_PORT=$SURGARD_PORT|" \
      -e "s|^DATABASE_URL=.*|DATABASE_URL=sqlite:///$APP_DIR/data/alarmhub.db|" \
      "$APP_DIR/alarmhub.env.example" > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  NEW_INSTALL=1
else
  say "Se conserva la configuración existente ($ENV_FILE)"
  WEB_PORT=$(grep -E '^WEB_PORT=' "$ENV_FILE" | cut -d= -f2 || echo "$WEB_PORT")
  DC09_PORT=$(grep -E '^DC09_TCP_PORT=' "$ENV_FILE" | cut -d= -f2 || echo "$DC09_PORT")
  SURGARD_PORT=$(grep -E '^SURGARD_PORT=' "$ENV_FILE" | cut -d= -f2 || echo "$SURGARD_PORT")
  NEW_INSTALL=0
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

say "Inicializando base de datos"
cd "$APP_DIR"
if [[ $NEW_INSTALL -eq 1 || -n "$ADMIN_PASSWORD" ]]; then
  [[ -n "$ADMIN_PASSWORD" ]] || ADMIN_PASSWORD=$("$PY" -c 'import secrets; print(secrets.token_urlsafe(12))')
  run_as env ALARMHUB_ENV_FILE="$ENV_FILE" "$APP_DIR/.venv/bin/python" -m alarmhub create-admin admin \
       --password "$ADMIN_PASSWORD" >/dev/null
  SHOW_PASSWORD=1
else
  run_as env ALARMHUB_ENV_FILE="$ENV_FILE" "$APP_DIR/.venv/bin/python" -m alarmhub init >/dev/null
fi

# ---------------------------------------------------------------- servicio
if [[ $SYSTEMD -eq 1 ]] && command -v systemctl >/dev/null; then
  say "Creando servicio systemd"
  cat > /etc/systemd/system/alarmhub.service <<UNIT
[Unit]
Description=AlarmHub - receptora de alarmas SIA DC-09
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
Environment=ALARMHUB_ENV_FILE=$ENV_FILE
ExecStart=$APP_DIR/.venv/bin/python -m alarmhub run
Restart=always
RestartSec=3
LimitNOFILE=65536
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$APP_DIR
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable --now alarmhub >/dev/null
  systemctl restart alarmhub
fi

# ---------------------------------------------------------------- cortafuegos
if [[ $FIREWALL -eq 1 ]]; then
  if command -v ufw >/dev/null && ufw status | grep -q active; then
    say "Abriendo puertos en ufw"
    ufw allow "$WEB_PORT/tcp" >/dev/null; ufw allow "$DC09_PORT/tcp" >/dev/null; ufw allow "$DC09_PORT/udp" >/dev/null
    [[ "$SURGARD_PORT" != "0" ]] && ufw allow "$SURGARD_PORT/tcp" >/dev/null
  elif command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then
    say "Abriendo puertos en firewalld"
    firewall-cmd -q --permanent --add-port="$WEB_PORT/tcp" --add-port="$DC09_PORT/tcp" --add-port="$DC09_PORT/udp"
    [[ "$SURGARD_PORT" != "0" ]] && firewall-cmd -q --permanent --add-port="$SURGARD_PORT/tcp"
    firewall-cmd -q --reload
  fi
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
say "AlarmHub instalado correctamente"
echo "   Web:            http://${IP:-localhost}:$WEB_PORT"
echo "   SIA DC-09:      TCP/UDP $DC09_PORT"
[[ "$SURGARD_PORT" != "0" ]] && echo "   Sur-Gard MLR2:  TCP $SURGARD_PORT"
echo "   API:            http://${IP:-localhost}:$WEB_PORT/api/docs"
if [[ "${SHOW_PASSWORD:-0}" -eq 1 ]]; then
  echo "   Usuario:        admin"
  echo "   Contraseña:     $ADMIN_PASSWORD   (guárdela; no se volverá a mostrar)"
fi
echo "   Configuración:  $ENV_FILE"
echo "   Logs:           journalctl -u alarmhub -f"
echo "   Simulador:      $APP_DIR/.venv/bin/python $APP_DIR/tools/dc09_sim.py --port $DC09_PORT --account 1234 --sia 'Nri1/BA3'"
