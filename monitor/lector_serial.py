import math
import random
import threading
import time
from collections import deque
from datetime import datetime

import serial
from serial.tools import list_ports

from . import alertas, config, registro_csv

# Estado compartido con el servidor web
ultima_lectura: dict | None = None
recientes: deque = deque(maxlen=300)
estado_conexion = {"conectado": False, "mensaje": "Iniciando..."}
ultima_recepcion: float | None = None
_ambiente: dict = {}  # última lectura del DHT11: temp, hum, instante
_sustrato: dict = {}  # última lectura del DS18B20: temp, instante
_ph: dict = {}        # última lectura del PH-4502C: ph, voltaje, instante
# Calibración guardada en el Arduino (voltaje_pH7 / voltaje_pH4) y confirmaciones recibidas
calibracion: dict = {"v7": None, "v4": None, "calibrado": False}
confirmaciones = {7: 0, 4: 0}
BAUDIOS_ARDUINO = 9600  # el sketch del Arduino UNO habla a 9600
COMANDOS_VALIDOS = {"CAL7", "CAL4"}
_comandos: deque = deque(maxlen=5)  # pendientes de entregar al ESP32 (modo WiFi)
_puerto_serial = None               # puerto abierto (modo serial)
_sim = {"v7": 2.5, "v4": 3.04, "v": 2.5, "flags": 3}  # estado del pH simulado
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


def parsear_ph(linea: str) -> tuple[float, float] | None:
    """Interpreta 'PH,<pH>,<voltaje_V>' del PH-4502C. None si no es válida."""
    partes = linea.strip().split(",")
    if len(partes) != 3 or partes[0] != "PH":
        return None
    try:
        ph, voltaje = float(partes[1]), float(partes[2])
    except ValueError:
        return None
    if not (0 <= ph <= 14 and 0 <= voltaje <= 5):
        return None
    return ph, voltaje


def parsear_calibracion(linea: str) -> tuple[float, float, int] | None:
    """Interpreta 'CAL,<v_pH7>,<v_pH4>,<flags>' (flags: bit0 = pH7, bit1 = pH4 guardados)."""
    partes = linea.strip().split(",")
    if len(partes) != 4 or partes[0] != "CAL":
        return None
    try:
        v7, v4, flags = float(partes[1]), float(partes[2]), int(partes[3])
    except ValueError:
        return None
    if not (0 < v7 < 5 and 0 < v4 < 5 and 0 <= flags <= 3):
        return None
    return v7, v4, flags


def voltaje_ph_actual() -> float | None:
    """Último voltaje del sensor de pH, o None si no es reciente."""
    if _ph and time.time() - _ph["instante"] <= AMBIENTE_VIGENCIA:
        return _ph["voltaje"]
    return None


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

    # Y con el pH (PH-4502C)
    if voltaje_ph_actual() is not None:
        lectura["ph"] = _ph["ph"]
        lectura["ph_voltaje"] = _ph["voltaje"]
    else:
        lectura["ph"] = lectura["ph_voltaje"] = ""

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
    # Sin calibrar el pH no es fiable: se muestra pero no dispara alertas
    if lectura["ph"] != "" and calibracion["calibrado"]:
        valores["ph"] = lectura["ph"]
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
    ph = parsear_ph(linea)
    if ph is not None:
        _ph.update(ph=ph[0], voltaje=ph[1], instante=time.time())
        return True
    cal = parsear_calibracion(linea)
    if cal is not None:
        calibracion.update(v7=cal[0], v4=cal[1], calibrado=cal[2] == 3)
        return True
    if linea.strip() in ("ACK,7", "ACK,4"):
        confirmaciones[int(linea.strip()[-1])] += 1
        return True
    lectura = parsear(linea)
    if lectura is None:
        return False
    _procesar(lectura)
    return True


def enviar_por_puerto(comando: str, puerto: str) -> str | None:
    """Envía el comando directo al Arduino por el COM indicado y espera su ACK.

    Devuelve None si se confirmó, o un texto con el motivo del fallo.
    """
    punto = int(comando[-1])
    try:
        # Abrir el puerto reinicia el Arduino UNO: hay que esperar a que arranque
        with serial.Serial(puerto, BAUDIOS_ARDUINO, timeout=1) as p:
            time.sleep(2.5)
            p.reset_input_buffer()
            p.write((comando + "\n").encode())
            limite = time.time() + 5
            while time.time() < limite:
                linea = p.readline().decode("utf-8", errors="ignore").strip()
                if linea == f"ACK,{punto}":
                    confirmaciones[punto] += 1
                    return None
                if linea:
                    recibir_linea(linea)
    except serial.SerialException as e:
        return f"no se pudo abrir {puerto} (¿está abierto el Monitor Serial u otro programa?): {e}"
    return f"{puerto} abierto pero el Arduino no respondió (revisa que sea el puerto correcto y el sketch cargado)"


def enviar_comando(comando: str) -> bool:
    """Pide al Arduino un comando de calibración (CAL7 / CAL4). False si no se pudo entregar."""
    if comando not in COMANDOS_VALIDOS:
        return False
    if config.ENTRADA == "simulacion":
        punto = int(comando[-1])
        _sim["v7" if punto == 7 else "v4"] = _sim["v"]
        _sim["flags"] |= 1 if punto == 7 else 2
        recibir_linea(f"ACK,{punto}")
        return True
    if config.ENTRADA == "serial":
        try:
            _puerto_serial.write((comando + "\n").encode())
            return True
        except (AttributeError, serial.SerialException):
            return False
    _comandos.append(comando)  # el ESP32 lo recoge en la respuesta de su próximo envío
    return True


def listar_puertos() -> list[str]:
    return [p.device for p in list_ports.comports()]


def tomar_comando() -> str:
    """Siguiente comando pendiente para el ESP32 ('' si no hay)."""
    try:
        return _comandos.popleft()
    except IndexError:
        return ""


def estado_actual() -> dict:
    """Estado de conexión; en modo WiFi se considera caído si no llegan datos."""
    if config.ENTRADA == "wifi":
        activo = ultima_recepcion is not None and time.time() - ultima_recepcion < SEGUNDOS_SIN_DATOS
        mensaje = "Recibiendo datos del ESP32" if activo else "Esperando datos del ESP32 (WiFi)"
        return {"conectado": activo, "mensaje": mensaje}
    return dict(estado_conexion)


def _bucle_serial() -> None:
    global _puerto_serial
    while True:
        try:
            with serial.Serial(config.PUERTO_SERIAL, config.BAUDIOS, timeout=5) as puerto:
                _puerto_serial = puerto
                estado_conexion.update(conectado=True, mensaje=f"Conectado a {config.PUERTO_SERIAL}")
                while True:
                    linea = puerto.readline().decode("utf-8", errors="ignore")
                    recibir_linea(linea)
        except serial.SerialException as e:
            _puerto_serial = None
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
        # pH simulado: el voltaje sigue la recta de la calibración vigente
        ph = 7.0 + 0.5 * math.sin(t / 35)
        _sim["v"] = _sim["v7"] + (7 - ph) * (_sim["v4"] - _sim["v7"]) / 3 + random.uniform(-.005, .005)
        recibir_linea(f"CAL,{_sim['v7']:.3f},{_sim['v4']:.3f},{_sim['flags']}")
        recibir_linea(f"PH,{ph:.2f},{_sim['v']:.3f}")
        recibir_linea(f"SUE,{21.5 + 4 * math.sin(t / 40):.1f}")
        recibir_linea(f"HUM,{cruda},{int(pct < 70)}")
        t += 1
        time.sleep(2)


def iniciar() -> None:
    """Arranca el origen de datos. En modo WiFi no hay hilo: los datos llegan por HTTP."""
    destino = {"serial": _bucle_serial, "simulacion": _bucle_simulado}.get(config.ENTRADA)
    if destino:
        threading.Thread(target=destino, daemon=True).start()
