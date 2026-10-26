import hmac

from flask import Flask, jsonify, render_template, request

from . import config, descubrimiento, lector_serial, registro_csv

app = Flask(__name__)


@app.route("/")
def inicio():
    minimo, maximo, _ = config.LIMITES["humedad"]
    # Límites por sensor para colorear y avisar en la página (solo los configurados)
    limites = {}
    for nombre, (mn, mx, _) in config.LIMITES.items():
        crit_min, crit_max = config.CRITICOS.get(nombre, (None, None))
        limites[nombre] = {"min": mn, "max": mx, "crit_min": crit_min, "crit_max": crit_max}
    return render_template("index.html", hum_min=minimo, hum_max=maximo, limites=limites)


@app.route("/api/actual")
def actual():
    return jsonify(
        lectura=lector_serial.ultima_lectura,
        conexion=lector_serial.estado_actual(),
    )


@app.route("/api/historial")
def historial():
    horas = request.args.get("horas", default=24, type=float)
    return jsonify(registro_csv.leer_historial(horas))


@app.route("/api/lectura", methods=["POST"])
def recibir_lectura():
    """Recibe una línea del ESP32 (texto plano, ej: 'HUM,512,0')."""
    clave = request.headers.get("X-API-Key", "")
    if not config.API_KEY or not hmac.compare_digest(clave.encode(), config.API_KEY.encode()):
        return jsonify(error="clave inválida"), 401
    linea = request.get_data(as_text=True)[:200]
    if not lector_serial.recibir_linea(linea):
        return jsonify(error="línea no válida"), 400
    respuesta = {"ok": True}
    comando = lector_serial.tomar_comando()  # calibración pedida desde la web
    if comando:
        respuesta["cmd"] = comando
    return jsonify(respuesta)


@app.route("/api/calibracion")
def estado_calibracion():
    """Voltaje en vivo del sensor de pH y calibración guardada en el Arduino."""
    ph = lector_serial.ultima_lectura.get("ph") if lector_serial.ultima_lectura else None
    return jsonify(
        voltaje=lector_serial.voltaje_ph_actual(),
        ph=ph if ph != "" else None,
        voltaje_pH7=lector_serial.calibracion["v7"],
        voltaje_pH4=lector_serial.calibracion["v4"],
        calibrado=lector_serial.calibracion["calibrado"],
        confirmaciones=lector_serial.confirmaciones,
    )


@app.route("/api/calibracion", methods=["POST"])
def calibrar():
    """Guarda el voltaje actual como punto de calibración: {"punto": 7} o {"punto": 4}."""
    punto = (request.get_json(silent=True) or {}).get("punto")
    if punto not in (7, 4):
        return jsonify(error="punto debe ser 7 o 4"), 400
    if lector_serial.voltaje_ph_actual() is None:
        return jsonify(error="sin lectura reciente del sensor de pH"), 409
    if not lector_serial.enviar_comando(f"CAL{punto}"):
        return jsonify(error="no se pudo enviar el comando al Arduino"), 503
    return jsonify(ok=True)


def main() -> None:
    registro_csv.preparar()
    if config.ENTRADA == "wifi":
        if not config.API_KEY:
            print("AVISO: falta API_KEY en .env; el ESP32 no podrá enviar datos.")
        descubrimiento.iniciar()
    lector_serial.iniciar()
    print(f"Origen de datos: {config.ENTRADA} | Web en http://localhost:{config.PUERTO_WEB}")
    # 0.0.0.0: acepta conexiones de otros equipos de la red (el ESP32).
    # use_reloader=False: el reloader duplicaría los hilos en segundo plano.
    app.run(host="0.0.0.0", port=config.PUERTO_WEB, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
