"""Campos de datos extendidos de ANSI/SIA DC-09 (bloques ``[<letra><valor>]``
que siguen al bloque de datos principal)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class XField:
    letter: str
    name: str
    description: str
    max_len: int
    placeholder: str = ""


XFIELDS: dict[str, XField] = {f.letter: f for f in [
    XField("I", "Texto de alarma", "Descripción o comentario del evento (Win-1252)", 256, "Puerta principal forzada"),
    XField("V", "Verificación (URL)", "Enlace a audio/vídeo/imagen de verificación", 256,
           "https://video.example.com/clip/123"),
    XField("L", "Ubicación", "Ubicación del evento dentro de la instalación", 256, "Planta baja"),
    XField("R", "Sala / estancia", "Habitación o sala", 256, "Salón"),
    XField("O", "Edificio", "Nombre del edificio", 256, "Nave 2"),
    XField("S", "Nombre del sitio", "Nombre de la instalación", 256, "Joyería Centro"),
    XField("X", "Longitud", "Coordenada GPS (p. ej. E003.7038 o -3.7038)", 12, "E003.7038"),
    XField("Y", "Latitud", "Coordenada GPS (p. ej. N40.4168 o 40.4168)", 12, "N40.4168"),
    XField("Z", "Altitud", "Altitud en metros", 12, "650"),
    XField("H", "Hora del suceso", "Hora real del evento (puede diferir de la del mensaje)", 21,
           "10:05:00,11-11-2026"),
    XField("M", "Dirección MAC", "MAC del equipo transmisor (12 hex)", 12, "001122AABBCC"),
    XField("N", "Dirección de red", "Dirección IP / red del transmisor", 128, "192.168.1.50"),
    XField("J", "Vía de red", "Identificador del camino de comunicación (fabricante)", 1, "1"),
    XField("T", "Disparo de alarma", "Identificador de disparo (fabricante)", 1, "1"),
    XField("C", "Categoría de supervisión", "Nº de vías y categoría de supervisión (EN 50136)", 64, "SP4"),
    XField("A", "Hash de autenticación", "Hash del mensaje para autenticación", 12, ""),
    XField("P", "Hash de autenticación (largo)", "Hash del mensaje para autenticación", 256, ""),
    XField("K", "Clave de cifrado", "Petición de intercambio de clave (hasta 256 bits)", 64, ""),
]}

# Orden recomendado al construir la trama
ORDER = "IVLROSXYZHMNJTCAPK"


def clean(value: str, max_len: int) -> str:
    """Los corchetes delimitan bloques y no pueden aparecer dentro del valor."""
    return str(value).replace("[", "(").replace("]", ")").replace("\r", " ").replace("\n", " ")[:max_len]


def normalize(xdata: dict[str, str] | None) -> list[tuple[str, str]]:
    out = []
    for letter in sorted((xdata or {}).keys(), key=lambda k: ORDER.find(k) if k in ORDER else 99):
        value = (xdata or {})[letter]
        if value is None or str(value).strip() == "":
            continue
        letter = letter.upper()[:1]
        f = XFIELDS.get(letter)
        out.append((letter, clean(str(value).strip(), f.max_len if f else 256)))
    return out
