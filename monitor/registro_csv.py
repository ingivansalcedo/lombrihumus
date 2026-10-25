import csv
import shutil
import threading
from datetime import datetime, timedelta

from . import config

CAMPOS = [
    "timestamp", "humedad_cruda", "humedad_pct", "humedad_d0",
    "temp_ambiente", "hum_ambiente", "temp_sustrato",
]
_candado = threading.Lock()


def preparar() -> None:
    """Al arrancar: deja el CSV con el encabezado actual.

    Cubre archivos con un esquema anterior (menos columnas) o sin encabezado.
    Antes de reescribir guarda una copia en lecturas.csv.bak.
    """
    ruta = config.ARCHIVO_CSV
    if not ruta.exists() or ruta.stat().st_size == 0:
        return
    with _candado:
        with open(ruta, newline="", encoding="utf-8") as f:
            if next(csv.reader(f), None) == CAMPOS:
                return
        shutil.copy2(ruta, ruta.with_suffix(".csv.bak"))
        with open(ruta, newline="", encoding="utf-8") as f:
            filas = [
                fila for fila in csv.DictReader(f, fieldnames=CAMPOS, restval="")
                if fila["timestamp"] != "timestamp"  # descarta el encabezado viejo
            ]
        with open(ruta, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CAMPOS)
            w.writeheader()
            w.writerows(filas)


def guardar(lectura: dict) -> None:
    """Agrega una fila al CSV (crea el archivo con encabezado si no existe)."""
    config.ARCHIVO_CSV.parent.mkdir(exist_ok=True)
    with _candado:
        nuevo = not config.ARCHIVO_CSV.exists()
        with open(config.ARCHIVO_CSV, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CAMPOS)
            if nuevo:
                w.writeheader()
            w.writerow({c: lectura.get(c, "") for c in CAMPOS})


def leer_historial(horas: float = 24, max_puntos: int = 1000) -> list[dict]:
    """Devuelve las lecturas de las últimas `horas`, reducidas a `max_puntos`."""
    if not config.ARCHIVO_CSV.exists():
        return []
    desde = datetime.now() - timedelta(hours=horas)
    filas = []
    with _candado, open(config.ARCHIVO_CSV, newline="", encoding="utf-8") as f:
        # fieldnames fijos: no depende de que el archivo conserve su encabezado;
        # restval="" completa las filas del esquema anterior (sin ambiente).
        for fila in csv.DictReader(f, fieldnames=CAMPOS, restval=""):
            try:
                if datetime.fromisoformat(fila["timestamp"]) >= desde:
                    filas.append(fila)
            except (KeyError, ValueError):
                continue  # encabezado o fila dañada
    paso = max(1, len(filas) // max_puntos)
    return filas[::paso]
