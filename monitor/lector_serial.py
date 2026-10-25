import math
import random
import threading
import time
from collections import deque
from datetime import datetime

import serial

from . import alertas, config, registro_csv

# Estado compartido con el servidor web
ultima_lectura: dict | None = None
recientes: deque = deque(maxlen=300)
estado_conexion = {"conectado": False, "mensaje": "Iniciando..."}
ultima_recepcion: float | None = None
_ambiente: dict = {}  # última lectura del DHT11: temp, hum, instante
_sustrato: dict = {}  # última lectura del DS18B20: temp, instante
AMBIENTE_VIGENCIA = 10  # s; pasado este tiempo el dato del DHT11/DS18B20 se considera perdido
SEGUNDOS_SIN_DATOS = 15 # sin lecturas durante este tiempo => se muestra desconectado


def cruda_a_porcentaje(cruda: int) -> float:
    """Convierte el valor analógico del LM393 a % de humedad (0-100)."""
    span = config.ADC_SECO - config.ADC_MOJADO
    pct = (config.ADC_SECO - cruda) / span * 100
    return round(max(0.0, min(100.0, pct)), 1)


def parsear(linea: str) -> dict | None:
    """Interpreta 'HUM,<cruda>,<d0>'. Devuelve None si la línea no es válida."""
    partes = linea.strip().split(",")
    if len(partes) != 3 or partes[0] != "HUM":
        return None
    try:
        cruda, d0 = int(partes[1]), int(partes[2])
    except ValueError:
        return None
    if not 0 <= cruda <= 1023:
        return None
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "humedad_cruda": cruda,
        "humedad_pct": cruda_a_porcentaje(cruda),
        "humedad_d0": d0,
    }


def parsear_ambiente(linea: str) -> tuple[float, float] | None:
    """Interpreta 'AMB,<temp_C>,<humedad_%>' del DHT11. None si no es válida."""
    partes = linea.strip().split(",")
    if len(partes) != 3 or partes[0] != "AMB":
        return None
    try:
        temp, hum = float(partes[1]), float(partes[2])
    except ValueError:
        return None
    # Rangos físicos amplios: descarta lecturas corruptas (NaN incluido)
    if not (-40 <= temp <= 80 and 0 <= hum <= 100):
        return None
    return temp, hum


def parsear_sustrato(linea: str) -> float | None:
    """Interpreta 'SUE,<temp_C>' del DS18B20. None si no es válida."""
    partes = linea.strip().split(",")
    if len(partes) != 2 or partes[0] != "SUE":
        return None
    try:
        temp = float(partes[1])
    except ValueError:
        return None
    # Rango de operación del DS18B20; descarta NaN y el -127 de sensor desconectado
    if not -55 <= temp <= 125 or temp == -127:
        return None
    return temp


def _procesar(lectura: dict) -> None:
    global ultima_lectura, ultima_recepcion
    # Une la última lectura del DHT11 si es reciente; si no, deja las columnas vacías
    if _ambiente and time.time() - _ambiente["instante"] <= AMBIENTE_VIGENCIA:
        lectura["temp_ambiente"] = _ambiente["temp"]
        lectura["hum_ambiente"] = _ambiente["hum"]
    else:
        lectura["temp_ambiente"] = lectura["hum_ambiente"] = ""

    # Igual con la temperatura del sustrato (DS18B20)
    if _sustrato and time.time() - _sustrato["instante"] <= AMBIENTE_VIGENCIA:
        lectura["temp_sustrato"] = _sustrato["temp"]
    else:
        lectura["temp_sustrato"] = ""

    ultima_lectura = lectura
    ultima_recepcion = time.time()
    recientes.append(lectura)
    registro_csv.guardar(lectura)

    valores = {"humedad": lectura["humedad_pct"]}
    if lectura["temp_ambiente"] != "":
        valores["temp_ambiente"] = lectura["temp_ambiente"]
        valores["hum_ambiente"] = lectura["hum_ambiente"]
    if lectura["temp_sustrato"] != "":
        valores["temp_sustrato"] = lectura["temp_sustrato"]
    alertas.evaluar(valores)


def recibir_linea(linea: str) -> bool:
    """Procesa una línea recibida (por WiFi o serial). False si no es válida."""
    ambiente = parsear_ambiente(linea)
    if ambiente:
        # No genera fila: se une a la siguiente lectura HUM
        _ambiente.update(temp=ambiente[0], hum=ambiente[1], instante=time.time())
        return True
    sustrato = parsear_sustrato(linea)
    if sustrato is not None:
        _sustrato.update(temp=sustrato, instante=time.time())
        return True
    lectura = parsear(linea)
    if lectura is None:
        return False
    _procesar(lectura)
    return True


def estado_actual() -> dict:
    """Estado de conexión; en modo WiFi se considera caído si no llegan datos."""
    if config.ENTRADA == "wifi":
        activo = ultima_recepcion is not None and time.time() - ultima_recepcion < SEGUNDOS_SIN_DATOS
        mensaje = "Recibiendo datos del ESP32" if activo else "Esperando datos del ESP32 (WiFi)"
        return {"conectado": activo, "mensaje": mensaje}
    return dict(estado_conexion)


def _bucle_serial() -> None:
    while True:
        try:
            with serial.Serial(config.PUERTO_SERIAL, config.BAUDIOS, timeout=5) as puerto:
                estado_conexion.update(conectado=True, mensaje=f"Conectado a {config.PUERTO_SERIAL}")
                while True:
                    linea = puerto.readline().decode("utf-8", errors="ignore")
                    recibir_linea(linea)
        except serial.SerialException as e:
            estado_conexion.update(conectado=False, mensaje=f"Sin conexión con {config.PUERTO_SERIAL}")
            print(f"[Serial] {e}. Reintentando en 5 s...")
            time.sleep(5)


def _bucle_simulado() -> None:
    estado_conexion.update(conectado=True, mensaje="Modo simulación")
    t = 0
    while True:
        pct = 75 + 12 * math.sin(t / 20) + random.uniform(-1, 1)
        cruda = round(config.ADC_SECO - pct / 100 * (config.ADC_SECO - config.ADC_MOJADO))
        recibir_linea(f"AMB,{22 + 3 * math.sin(t / 30):.1f},{55 + 8 * math.sin(t / 25):.1f}")
        recibir_linea(f"SUE,{21.5 + 4 * math.sin(t / 40):.1f}")
        recibir_linea(f"HUM,{cruda},{int(pct < 70)}")
        t += 1
        time.sleep(2)


def iniciar() -> None:
    """Arranca el origen de datos. En modo WiFi no hay hilo: los datos llegan por HTTP."""
    destino = {"serial": _bucle_serial, "simulacion": _bucle_simulado}.get(config.ENTRADA)
    if destino:
        threading.Thread(target=destino, daemon=True).start()
