#!/usr/bin/env bash
# Instalador automático de AlarmSim – generador de alarmas SIA DC-09
# (Debian/Ubuntu, RHEL/Rocky/Alma, Fedora)
#
#   sudo ./install.sh
#   sudo ./install.sh --web-port 8080 --receiver-port 7100 --admin-password 'Secreta123'
#   sudo ./install.sh --uninstall
#
# Volver a ejecutarlo actualiza el código conservando configuración y datos.
set -euo pipefail

APP_DIR=/opt/alarmsim
APP_USER=alarmsim
WEB_PORT=8080
RECEIVER_PORT=7100
ADMIN_PASSWORD=""
SYSTEMD=1
FIREWALL=1
UNINSTALL=0
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() { cat <<USAGE
Uso: sudo ./install.sh [opciones]
  --dir DIR               Directorio de instalación (por defecto $APP_DIR)
  --user USUARIO          Usuario del servicio (por defecto $APP_USER)
  --web-port N            Puerto de la interfaz web (por defecto $WEB_PORT)
  --receiver-port N       Puerto del receptor de pruebas TCP/UDP (0 = desactivado; por defecto $RECEIVER_PORT)
  --admin-password PASS   Contraseña del usuario admin (si no, se genera una)
  --no-systemd            No crear el servicio systemd
  --no-firewall           No abrir puertos en ufw/firewalld
  --uninstall             Quitar el servicio (conserva los datos)
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) APP_DIR="$2"; shift 2;;
    --user) APP_USER="$2"; shift 2;;
    --web-port) WEB_PORT="$2"; shift 2;;
    --receiver-port) RECEIVER_PORT="$2"; shift 2;;
    --admin-password) ADMIN_PASSWORD="$2"; shift 2;;
    --no-systemd) SYSTEMD=0; shift;;
    --no-firewall) FIREWALL=0; shift;;
    --uninstall) UNINSTALL=1; shift;;
    -h|--help) usage; exit 0;;
    *) echo "Opción desconocida: $1"; usage; exit 1;;
  esac
done

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }
run_as() { if command -v runuser >/dev/null; then runuser -u "$APP_USER" -- "$@"; else sudo -u "$APP_USER" "$@"; fi; }

[[ $EUID -eq 0 ]] || die "Ejecute como root (sudo ./install.sh)"

if [[ $UNINSTALL -eq 1 ]]; then
  systemctl disable --now alarmsim 2>/dev/null || true
  rm -f /etc/systemd/system/alarmsim.service
  systemctl daemon-reload 2>/dev/null || true
  say "Servicio eliminado. Los datos siguen en $APP_DIR"
  exit 0
fi

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
  say "Gestor de paquetes no reconocido: se asume Python 3.10+ instalado"
fi
PY=$(command -v python3) || die "python3 no encontrado"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || die "Se necesita Python 3.10 o superior"

if ! id "$APP_USER" >/dev/null 2>&1; then
  say "Creando usuario de sistema $APP_USER"
  useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi

say "Copiando aplicación a $APP_DIR"
mkdir -p "$APP_DIR/data"
if [[ "$SRC_DIR" != "$APP_DIR" ]]; then
  rsync -a --delete --exclude '.git' --exclude 'data' --exclude 'alarmsim.env' --exclude '.venv' \
        --exclude '__pycache__' "$SRC_DIR/" "$APP_DIR/"
fi

say "Creando entorno virtual e instalando librerías"
[[ -d "$APP_DIR/.venv" ]] || "$PY" -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

ENV_FILE="$APP_DIR/alarmsim.env"
if [[ ! -f "$ENV_FILE" ]]; then
  say "Generando $ENV_FILE"
  SECRET=$("$PY" -c 'import secrets; print(secrets.token_urlsafe(48))')
  sed -e "s|^SECRET_KEY=.*|SECRET_KEY=$SECRET|" \
      -e "s|^WEB_PORT=.*|WEB_PORT=$WEB_PORT|" \
      -e "s|^TEST_RECEIVER_PORT=.*|TEST_RECEIVER_PORT=$RECEIVER_PORT|" \
      -e "s|^DATABASE_URL=.*|DATABASE_URL=sqlite:///$APP_DIR/data/alarmsim.db|" \
      "$APP_DIR/alarmsim.env.example" > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  NEW_INSTALL=1
else
  say "Se conserva la configuración existente ($ENV_FILE)"
  WEB_PORT=$(grep -E '^WEB_PORT=' "$ENV_FILE" | cut -d= -f2 || echo "$WEB_PORT")
  RECEIVER_PORT=$(grep -E '^TEST_RECEIVER_PORT=' "$ENV_FILE" | cut -d= -f2 || echo "$RECEIVER_PORT")
  NEW_INSTALL=0
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

say "Inicializando base de datos"
cd "$APP_DIR"
if [[ $NEW_INSTALL -eq 1 || -n "$ADMIN_PASSWORD" ]]; then
  [[ -n "$ADMIN_PASSWORD" ]] || ADMIN_PASSWORD=$("$PY" -c 'import secrets; print(secrets.token_urlsafe(12))')
  run_as env ALARMSIM_ENV_FILE="$ENV_FILE" "$APP_DIR/.venv/bin/python" -m alarmsim set-password admin \
       --password "$ADMIN_PASSWORD" >/dev/null
  SHOW_PASSWORD=1
else
  run_as env ALARMSIM_ENV_FILE="$ENV_FILE" "$APP_DIR/.venv/bin/python" -m alarmsim init >/dev/null
fi

if [[ $SYSTEMD -eq 1 ]] && command -v systemctl >/dev/null; then
  say "Creando servicio systemd 'alarmsim'"
  cat > /etc/systemd/system/alarmsim.service <<UNIT
[Unit]
Description=AlarmSim - generador de alarmas SIA DC-09
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
Environment=ALARMSIM_ENV_FILE=$ENV_FILE
ExecStart=$APP_DIR/.venv/bin/python -m alarmsim run
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
  systemctl enable alarmsim >/dev/null
  systemctl restart alarmsim
fi

if [[ $FIREWALL -eq 1 ]]; then
  if command -v ufw >/dev/null && ufw status | grep -q active; then
    say "Abriendo puertos en ufw"
    ufw allow "$WEB_PORT/tcp" >/dev/null
    if [[ "$RECEIVER_PORT" != "0" ]]; then ufw allow "$RECEIVER_PORT/tcp" >/dev/null; ufw allow "$RECEIVER_PORT/udp" >/dev/null; fi
  elif command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then
    say "Abriendo puertos en firewalld"
    firewall-cmd -q --permanent --add-port="$WEB_PORT/tcp"
    if [[ "$RECEIVER_PORT" != "0" ]]; then firewall-cmd -q --permanent --add-port="$RECEIVER_PORT/tcp" --add-port="$RECEIVER_PORT/udp"; fi
    firewall-cmd -q --reload
  fi
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
say "AlarmSim instalado"
echo "   Web:                 http://${IP:-localhost}:$WEB_PORT"
[[ "$RECEIVER_PORT" != "0" ]] && echo "   Receptor de pruebas: TCP/UDP $RECEIVER_PORT"
echo "   API:                 http://${IP:-localhost}:$WEB_PORT/api/docs"
if [[ "${SHOW_PASSWORD:-0}" -eq 1 ]]; then
  echo "   Usuario:             admin"
  echo "   Contraseña:          $ADMIN_PASSWORD   (guárdela; no se volverá a mostrar)"
fi
echo "   Configuración:       $ENV_FILE"
echo "   Logs:                journalctl -u alarmsim -f"
echo "   Consola:             $APP_DIR/.venv/bin/python -m alarmsim send --help"
