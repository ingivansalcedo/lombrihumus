import os
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

PUERTO_SERIAL = os.getenv("PUERTO_SERIAL", "COM6")
BAUDIOS = int(os.getenv("BAUDIOS", "115200"))
SIMULAR = os.getenv("SIMULAR", "0") == "1"

# Origen de los datos: "wifi" (ESP32 envía por HTTP) o "serial" (cable USB).
ENTRADA = "simulacion" if SIMULAR else os.getenv("ENTRADA", "wifi").lower()

# Servidor web / recepción por WiFi
PUERTO_WEB = int(os.getenv("PUERTO_WEB", "5000"))
PUERTO_UDP = 4210  # puerto donde el ESP32 escucha la baliza del servidor
# Clave compartida con el ESP32 (secrets.h). Si está vacía, /api/lectura se rechaza.
API_KEY = os.getenv("API_KEY", "")

ADC_SECO = int(os.getenv("ADC_SECO", "1000"))
ADC_MOJADO = int(os.getenv("ADC_MOJADO", "400"))

# Límites por sensor: nombre -> (mínimo, máximo, unidad).
# Para agregar temperatura, pH, etc. basta con añadir una entrada aquí.
LIMITES = {
    "humedad": (
        float(os.getenv("HUMEDAD_MIN", "70")),
        float(os.getenv("HUMEDAD_MAX", "80")),
        "%",
    ),
}

def _limite_opcional(nombre, var_min, var_max, unidad):
    """Registra límites solo si ambas variables están en el .env (si no, sin alertas)."""
    minimo, maximo = os.getenv(var_min), os.getenv(var_max)
    if minimo and maximo:
        LIMITES[nombre] = (float(minimo), float(maximo), unidad)


# Temperatura del sustrato (DS18B20): ideal 18–25 °C, configurable en el .env
LIMITES["temp_sustrato"] = (
    float(os.getenv("TEMP_SUS_MIN", "18")),
    float(os.getenv("TEMP_SUS_MAX", "25")),
    "°C",
)

_limite_opcional("temp_ambiente", "TEMP_AMB_MIN", "TEMP_AMB_MAX", "°C")
_limite_opcional("hum_ambiente", "HUM_AMB_MIN", "HUM_AMB_MAX", "%")

# Límites críticos (riesgo para las lombrices): nombre -> (mínimo, máximo); None = sin límite.
# Se pueden cambiar en el .env con <PREFIJO>_CRIT_MIN / <PREFIJO>_CRIT_MAX (vacío = sin límite).
CRITICOS: dict[str, tuple[float | None, float | None]] = {}


def _critico(nombre, prefijo, minimo_def, maximo_def):
    if nombre not in LIMITES:
        return
    valores = []
    for sufijo, defecto in (("MIN", minimo_def), ("MAX", maximo_def)):
        crudo = os.getenv(f"{prefijo}_CRIT_{sufijo}", defecto)
        valores.append(float(crudo) if crudo not in (None, "") else None)
    CRITICOS[nombre] = (valores[0], valores[1])


_critico("humedad", "HUMEDAD", "60", "90")          # deshidratación / falta de oxígeno
_critico("temp_sustrato", "TEMP_SUS", "10", "30")   # letargo / estrés térmico en el lecho
_critico("temp_ambiente", "TEMP_AMB", "10", "30")   # letargo / estrés térmico
_critico("hum_ambiente", "HUM_AMB", "40", "")       # costra seca en la superficie

ALERTA_CRITICA_ESPERA_MIN = int(os.getenv("ALERTA_CRITICA_ESPERA_MIN", "5"))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
ALERTA_ESPERA_MIN = int(os.getenv("ALERTA_ESPERA_MIN", "30"))

ARCHIVO_CSV = Path(os.getenv("ARCHIVO_CSV", RAIZ / "datos" / "lecturas.csv"))
