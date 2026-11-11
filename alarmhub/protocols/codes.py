"""Tablas de códigos SIA (DC-03/DC-04) y Ademco Contact ID (DC-05).

Cada evento se normaliza a una *categoría* común, independiente del protocolo:

    alarm, fire, panic, medical, tamper, trouble, supervision,
    restore, open, close, bypass, test, info
"""
from __future__ import annotations

CATEGORIES = {
    "fire": "Incendio",
    "panic": "Pánico / Atraco",
    "medical": "Emergencia médica",
    "alarm": "Intrusión",
    "tamper": "Sabotaje",
    "supervision": "Supervisión / Comunicación",
    "trouble": "Avería",
    "restore": "Restauración",
    "open": "Desconexión (apertura)",
    "close": "Conexión (cierre)",
    "bypass": "Anulación de zona",
    "test": "Test / Prueba",
    "info": "Informativo",
}

DEFAULT_PRIORITY = {
    "fire": 1, "panic": 1, "medical": 1, "alarm": 2, "tamper": 3, "supervision": 3,
    "trouble": 4, "restore": 5, "open": 5, "close": 5, "bypass": 5, "test": 5, "info": 5,
}

# código SIA -> (descripción, categoría)
SIA_CODES: dict[str, tuple[str, str]] = {
    "AR": ("Restauración de red eléctrica", "restore"),
    "AT": ("Fallo de red eléctrica (230V)", "trouble"),
    "BA": ("Alarma de robo", "alarm"),
    "BB": ("Anulación zona robo", "bypass"),
    "BC": ("Cancelación alarma robo", "info"),
    "BH": ("Restauración alarma robo", "restore"),
    "BJ": ("Restauración avería robo", "restore"),
    "BR": ("Restauración robo", "restore"),
    "BT": ("Avería zona robo", "trouble"),
    "BU": ("Desanulación zona robo", "info"),
    "BV": ("Alarma de robo verificada", "alarm"),
    "BX": ("Test de zona robo", "test"),
    "CA": ("Conexión automática", "close"),
    "CE": ("Extensión de cierre", "info"),
    "CF": ("Cierre forzado", "close"),
    "CG": ("Cierre de área", "close"),
    "CI": ("Fallo al conectar", "trouble"),
    "CL": ("Conexión (cierre) por usuario", "close"),
    "CP": ("Conexión automática", "close"),
    "CR": ("Cierre reciente", "info"),
    "CS": ("Conexión por llave", "close"),
    "CT": ("Cierre tardío", "trouble"),
    "DD": ("Acceso denegado", "info"),
    "DF": ("Puerta forzada", "alarm"),
    "DG": ("Acceso concedido", "info"),
    "DR": ("Restauración puerta", "restore"),
    "DT": ("Puerta abierta demasiado tiempo", "trouble"),
    "EA": ("Alarma salida", "alarm"),
    "ER": ("Restauración módulo expansión", "restore"),
    "ET": ("Avería módulo expansión", "trouble"),
    "FA": ("Alarma de incendio", "fire"),
    "FB": ("Anulación zona incendio", "bypass"),
    "FH": ("Restauración alarma incendio", "restore"),
    "FJ": ("Restauración avería incendio", "restore"),
    "FR": ("Restauración incendio", "restore"),
    "FT": ("Avería zona incendio", "trouble"),
    "FX": ("Test zona incendio", "test"),
    "GA": ("Alarma de gas", "fire"),
    "GR": ("Restauración gas", "restore"),
    "HA": ("Alarma de atraco / coacción", "panic"),
    "HR": ("Restauración atraco", "restore"),
    "JA": ("Sabotaje de código (intentos)", "tamper"),
    "KA": ("Alarma de calor", "fire"),
    "KR": ("Restauración calor", "restore"),
    "LB": ("Entrada en programación local", "info"),
    "LR": ("Restauración línea telefónica", "restore"),
    "LT": ("Fallo línea telefónica", "trouble"),
    "LX": ("Salida de programación local", "info"),
    "MA": ("Alarma médica", "medical"),
    "MR": ("Restauración médica", "restore"),
    "NR": ("Restauración red/IP", "restore"),
    "NT": ("Fallo de red/IP", "trouble"),
    "OA": ("Desconexión automática", "open"),
    "OG": ("Apertura de área", "open"),
    "OP": ("Desconexión (apertura) por usuario", "open"),
    "OR": ("Desconexión tras alarma", "open"),
    "OS": ("Desconexión por llave", "open"),
    "OT": ("Apertura tardía", "trouble"),
    "PA": ("Alarma de pánico", "panic"),
    "PR": ("Restauración pánico", "restore"),
    "QA": ("Emergencia", "medical"),
    "QR": ("Restauración emergencia", "restore"),
    "RP": ("Test periódico automático", "test"),
    "RR": ("Encendido / reinicio del sistema", "info"),
    "RS": ("Programación remota correcta", "info"),
    "RX": ("Test manual", "test"),
    "RY": ("Fin de test manual", "test"),
    "SA": ("Alarma de rociadores", "fire"),
    "SR": ("Restauración rociadores", "restore"),
    "TA": ("Sabotaje", "tamper"),
    "TR": ("Restauración sabotaje", "restore"),
    "UA": ("Alarma no tipificada", "alarm"),
    "UR": ("Restauración no tipificada", "restore"),
    "UT": ("Avería no tipificada", "trouble"),
    "WA": ("Alarma de inundación", "alarm"),
    "WR": ("Restauración inundación", "restore"),
    "XQ": ("Interferencia radio (jamming)", "tamper"),
    "XH": ("Restauración interferencia radio", "restore"),
    "XT": ("Batería baja de transmisor", "trouble"),
    "XR": ("Restauración batería transmisor", "restore"),
    "YC": ("Fallo de comunicación", "supervision"),
    "YK": ("Restauración comunicación", "restore"),
    "YM": ("Batería ausente", "trouble"),
    "YP": ("Avería fuente alimentación", "trouble"),
    "YQ": ("Restauración fuente alimentación", "restore"),
    "YR": ("Restauración batería", "restore"),
    "YS": ("Avería de comunicación", "supervision"),
    "YT": ("Batería baja del sistema", "trouble"),
    "YX": ("Servicio requerido", "trouble"),
    "ZA": ("Alarma de congelación", "alarm"),
    "ZR": ("Restauración congelación", "restore"),
    # Códigos internos de la plataforma
    "SUPV": ("Fallo de supervisión: el panel no comunica", "supervision"),
    "SUPR": ("Supervisión restaurada: el panel vuelve a comunicar", "restore"),
}

# Contact ID: código -> (descripción, categoría si nuevo evento, categoría si restauración)
CID_CODES: dict[str, tuple[str, str, str]] = {
    "100": ("Emergencia médica", "medical", "restore"),
    "101": ("Pulsador colgante médico", "medical", "restore"),
    "110": ("Alarma de incendio", "fire", "restore"),
    "111": ("Detector de humo", "fire", "restore"),
    "112": ("Combustión", "fire", "restore"),
    "113": ("Flujo de agua", "fire", "restore"),
    "114": ("Calor", "fire", "restore"),
    "115": ("Pulsador de incendio", "fire", "restore"),
    "117": ("Llama", "fire", "restore"),
    "120": ("Alarma de pánico", "panic", "restore"),
    "121": ("Coacción", "panic", "restore"),
    "122": ("Pánico silencioso", "panic", "restore"),
    "123": ("Pánico audible", "panic", "restore"),
    "130": ("Alarma de robo", "alarm", "restore"),
    "131": ("Robo perimetral", "alarm", "restore"),
    "132": ("Robo interior", "alarm", "restore"),
    "133": ("Robo 24 horas", "alarm", "restore"),
    "134": ("Robo entrada/salida", "alarm", "restore"),
    "135": ("Robo día/noche", "alarm", "restore"),
    "137": ("Sabotaje", "tamper", "restore"),
    "139": ("Robo verificado", "alarm", "restore"),
    "144": ("Sabotaje de sensor", "tamper", "restore"),
    "145": ("Sabotaje módulo expansión", "tamper", "restore"),
    "150": ("Alarma 24h no robo", "alarm", "restore"),
    "151": ("Detección de gas", "fire", "restore"),
    "154": ("Fuga de agua", "alarm", "restore"),
    "158": ("Temperatura alta", "alarm", "restore"),
    "159": ("Temperatura baja", "alarm", "restore"),
    "300": ("Avería del sistema", "trouble", "restore"),
    "301": ("Fallo de red eléctrica (230V)", "trouble", "restore"),
    "302": ("Batería baja del sistema", "trouble", "restore"),
    "305": ("Reinicio del sistema", "info", "info"),
    "309": ("Fallo test de batería", "trouble", "restore"),
    "311": ("Batería ausente", "trouble", "restore"),
    "321": ("Avería de sirena", "trouble", "restore"),
    "333": ("Avería módulo expansión", "trouble", "restore"),
    "344": ("Interferencia radio (jamming)", "tamper", "restore"),
    "350": ("Fallo de comunicación", "supervision", "restore"),
    "351": ("Fallo línea telefónica 1", "trouble", "restore"),
    "354": ("Fallo de comunicación con CRA", "supervision", "restore"),
    "373": ("Avería circuito incendio", "trouble", "restore"),
    "380": ("Avería de sensor", "trouble", "restore"),
    "381": ("Pérdida supervisión radio", "supervision", "restore"),
    "383": ("Sabotaje de sensor", "tamper", "restore"),
    "384": ("Batería baja de transmisor radio", "trouble", "restore"),
    "400": ("Desconexión / Conexión", "open", "close"),
    "401": ("Desconexión / Conexión por usuario", "open", "close"),
    "402": ("Desconexión / Conexión de grupo", "open", "close"),
    "403": ("Desconexión / Conexión automática", "open", "close"),
    "406": ("Cancelación por usuario", "info", "info"),
    "407": ("Desconexión / Conexión remota", "open", "close"),
    "408": ("Conexión rápida", "close", "close"),
    "409": ("Desconexión / Conexión por llave", "open", "close"),
    "441": ("Conexión parcial (modo stay)", "open", "close"),
    "455": ("Fallo de autoconexión", "trouble", "restore"),
    "456": ("Conexión parcial", "close", "close"),
    "457": ("Error de salida", "alarm", "restore"),
    "459": ("Cierre reciente", "info", "info"),
    "570": ("Anulación de zona", "bypass", "info"),
    "573": ("Anulación zona incendio", "bypass", "info"),
    "601": ("Test manual", "test", "test"),
    "602": ("Test periódico", "test", "test"),
    "603": ("Test periódico de transmisión RF", "test", "test"),
    "607": ("Modo test de paseo", "test", "test"),
    "627": ("Entrada en programación", "info", "info"),
    "628": ("Salida de programación", "info", "info"),
}


def sia_info(code: str) -> tuple[str, str]:
    code = code.upper()
    if code in SIA_CODES:
        return SIA_CODES[code]
    # Heurística por la segunda letra (A=alarma, R=restore, T=avería, B=anulación, X=test)
    second = code[1:2]
    cat = {"A": "alarm", "R": "restore", "H": "restore", "T": "trouble", "B": "bypass", "X": "test"}.get(second, "info")
    return (f"Evento SIA {code}", cat)


def cid_info(code: str, qualifier: str) -> tuple[str, str]:
    desc, new_cat, restore_cat = CID_CODES.get(code, (f"Evento Contact ID {code}", "", ""))
    if not new_cat:
        first = code[:1]
        new_cat = {"1": "alarm", "3": "trouble", "4": "open", "5": "bypass", "6": "test"}.get(first, "info")
        restore_cat = "close" if first == "4" else "restore"
    if qualifier == "3":
        return (desc + (" (conexión)" if restore_cat == "close" else " (restauración)"), restore_cat)
    if qualifier == "6":
        return (desc + " (estado)", "info")
    return (desc, new_cat)
