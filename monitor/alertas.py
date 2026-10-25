import threading
import time

import requests

from . import config

NOMBRES = {
    "humedad": "Humedad del sustrato",
    "temp_sustrato": "Temperatura del sustrato",
    "temp_ambiente": "Temperatura ambiente",
    "hum_ambiente": "Humedad relativa del ambiente",
}

# Niveles: 0 = en rango, 1 = advertencia (fuera del ideal), 2 = crítico
_nivel_previo: dict[str, int] = {}
_ultima_alerta: dict[str, float] = {}  # sensor -> instante del último aviso


def enviar_telegram(texto: str) -> bool:
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        print("[Telegram] sin configurar, mensaje no enviado:", texto)
        return False
    url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url, json={"chat_id": config.TELEGRAM_CHAT_ID, "text": texto}, timeout=10
        )
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        # No imprimir `e` completo: la URL incluye el token.
        print(f"[Telegram] error al enviar: {type(e).__name__}")
        return False


def _enviar_async(texto: str) -> None:
    threading.Thread(target=enviar_telegram, args=(texto,), daemon=True).start()


def nivel(sensor: str, valor: float) -> int:
    """0 = en rango, 1 = fuera del rango ideal, 2 = crítico."""
    minimo, maximo, _ = config.LIMITES[sensor]
    crit_min, crit_max = config.CRITICOS.get(sensor, (None, None))
    if (crit_min is not None and valor < crit_min) or (crit_max is not None and valor > crit_max):
        return 2
    if valor < minimo or valor > maximo:
        return 1
    return 0


def _mensaje(sensor: str, valor: float, nuevo: int, previo: int) -> str:
    minimo, maximo, unidad = config.LIMITES[sensor]
    nombre = NOMBRES.get(sensor, sensor)
    sentido = "BAJA" if valor < minimo else "ALTA"
    actual = f"Valor actual: {valor:.1f}{unidad}\nRango ideal: {minimo:g}–{maximo:g}{unidad}"
    if nuevo == 0:
        return f"✅ {nombre} volvió al rango ideal ({valor:.1f}{unidad})"
    if nuevo == 2:
        return f"🚨 CRÍTICO: {nombre} {sentido}\n{actual}\nRiesgo para las lombrices, requiere atención."
    if previo == 2:
        return f"⚠️ {nombre} salió de nivel crítico pero sigue {sentido}\n{actual}"
    return f"⚠️ {nombre} {sentido}\n{actual}"


def evaluar(valores: dict[str, float]) -> None:
    """Compara cada valor con sus límites y avisa por Telegram si corresponde.

    Avisa de inmediato cuando cambia el nivel (incluida la recuperación) y,
    mientras el nivel se mantiene, repite cada ALERTA_ESPERA_MIN minutos
    (advertencia) o ALERTA_CRITICA_ESPERA_MIN minutos (crítico).
    """
    ahora = time.time()
    for sensor, valor in valores.items():
        if sensor not in config.LIMITES:
            continue
        nuevo = nivel(sensor, valor)
        previo = _nivel_previo.get(sensor, 0)
        _nivel_previo[sensor] = nuevo

        if nuevo == 0 and previo == 0:
            continue

        espera = (config.ALERTA_CRITICA_ESPERA_MIN if nuevo == 2 else config.ALERTA_ESPERA_MIN) * 60
        if nuevo != previo or ahora - _ultima_alerta.get(sensor, 0) >= espera:
            _ultima_alerta[sensor] = ahora
            _enviar_async(_mensaje(sensor, valor, nuevo, previo))
