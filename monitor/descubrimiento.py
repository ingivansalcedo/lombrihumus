"""Baliza UDP: anuncia la IP del servidor en la red para que el ESP32 la encuentre
sin configurarla, sea cual sea la red (casa, hotspot del celular, etc.)."""
import socket
import threading
import time

from . import config


def _ips_locales() -> set[str]:
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith(("127.", "169.254.")):
                ips.add(ip)
    except socket.gaierror:
        pass
    return ips


def _bucle() -> None:
    mensaje = f"LOMBRI_SERVER:{config.PUERTO_WEB}".encode()
    while True:
        # Se recalculan las IP en cada vuelta: pueden cambiar si cambia la red.
        for ip in _ips_locales():
            destinos = ("255.255.255.255", ip.rsplit(".", 1)[0] + ".255")
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    s.bind((ip, 0))  # sale por esa interfaz concreta
                    for destino in destinos:
                        s.sendto(mensaje, (destino, config.PUERTO_UDP))
            except OSError:
                continue
        time.sleep(3)


def iniciar() -> None:
    threading.Thread(target=_bucle, daemon=True).start()
