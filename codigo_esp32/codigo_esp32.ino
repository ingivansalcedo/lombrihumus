#include <WiFi.h>
#include <WiFiMulti.h>
#include <WiFiUdp.h>
#include <HTTPClient.h>
#include "secrets.h"  // redes WiFi y API_KEY (ver secrets.h.example)

// Serial2 para hablar con el Arduino
// RX2 = GPIO 16 (Recibe la señal proveniente del divisor de voltaje)
// TX2 = GPIO 17 (Envía señal directa de 3.3V al RX del Arduino)
#define RXD2 16
#define TXD2 17

// El servidor Flask anuncia su IP por UDP (broadcast) en este puerto
#define PUERTO_UDP 4210
#define FALLOS_PARA_REDESCUBRIR 3

WiFiMulti wifiMulti;
WiFiUDP udp;

IPAddress servidorIP;
uint16_t servidorPuerto = 5000;
bool servidorConocido = false;
int fallosEnvio = 0;
bool wifiAnterior = false;

// Escucha la baliza del servidor ("LOMBRI_SERVER:<puerto>") y guarda su IP
void escucharBaliza() {
  int n = udp.parsePacket();
  if (n <= 0) return;

  char buf[48];
  int len = udp.read(buf, sizeof(buf) - 1);
  if (len <= 0) return;
  buf[len] = '\0';

  if (strncmp(buf, "LOMBRI_SERVER:", 14) != 0) return;

  IPAddress ip = udp.remoteIP();
  uint16_t puerto = atoi(buf + 14);
  if (!servidorConocido || ip != servidorIP) {
    Serial.print("Servidor encontrado: ");
    Serial.print(ip);
    Serial.print(":");
    Serial.println(puerto);
  }
  servidorIP = ip;
  servidorPuerto = puerto;
  servidorConocido = true;
}

// El servidor puede responder con un comando para el Arduino (calibración del pH
// pedida desde la web): {"cmd":"CAL7","ok":true}. Se reenvía por Serial2.
void reenviarComando(const String& respuesta) {
  int ini = respuesta.indexOf("\"cmd\":\"");
  if (ini < 0) return;
  ini += 7;
  int fin = respuesta.indexOf('"', ini);
  if (fin <= ini) return;
  String cmd = respuesta.substring(ini, fin);
  Serial.println("[Servidor -> Arduino] " + cmd);
  Serial2.println(cmd);
}

// Envía una línea del Arduino (ej: HUM,512,0) al servidor Flask por HTTP
void enviarAlServidor(const String& linea) {
  if (WiFi.status() != WL_CONNECTED || !servidorConocido) {
    Serial.println("Sin servidor, lectura no enviada: " + linea);
    return;
  }

  HTTPClient http;
  String url = "http://" + servidorIP.toString() + ":" + String(servidorPuerto) + "/api/lectura";
  http.begin(url);
  http.setTimeout(3000);
  http.addHeader("Content-Type", "text/plain");
  http.addHeader("X-API-Key", API_KEY);
  int codigo = http.POST(linea);
  String respuesta = (codigo == 200) ? http.getString() : "";
  http.end();

  if (codigo == 200) {
    fallosEnvio = 0;
    reenviarComando(respuesta);
  } else {
    Serial.print("Error al enviar (");
    Serial.print(codigo);
    Serial.println(")");
    // 401 = API_KEY distinta; no tiene sentido buscar otro servidor por eso
    if (codigo != 401 && ++fallosEnvio >= FALLOS_PARA_REDESCUBRIR) {
      servidorConocido = false;  // esperar nueva baliza (puede haber cambiado la IP)
      fallosEnvio = 0;
    }
  }
}

void setup() {
  // USB con la PC (solo para depuración)
  Serial.begin(115200);

  // Serial2 de hardware para comunicarse con el Arduino: 9600 baudios, 8N1
  Serial2.begin(9600, SERIAL_8N1, RXD2, TXD2);

  WiFi.mode(WIFI_STA);
  for (const RedWifi& red : REDES) {
    wifiMulti.addAP(red.ssid, red.clave);
  }
  udp.begin(PUERTO_UDP);

  Serial.println("--- ESP32 LISTO ---");
  Serial.println("Buscando red WiFi...");
}

void loop() {
  // Conecta (o reconecta) a la mejor red disponible de la lista
  bool wifiOk = (wifiMulti.run() == WL_CONNECTED);
  if (wifiOk && !wifiAnterior) {
    Serial.print("WiFi conectado a ");
    Serial.print(WiFi.SSID());
    Serial.print(" | IP: ");
    Serial.println(WiFi.localIP());
  }
  if (!wifiOk) {
    servidorConocido = false;  // al cambiar de red la IP del servidor será otra
  }
  wifiAnterior = wifiOk;

  if (wifiOk) escucharBaliza();

  // Reenvía cada línea que llega del Arduino
  while (Serial2.available() > 0) {
    String linea = Serial2.readStringUntil('\n');
    linea.trim();

    if (linea.length() > 0) {
      Serial.println("[Arduino] " + linea);
      enviarAlServidor(linea);

      // --- Prueba de conexión PING/PONG (desactivada) ---
      // Serial2.println("PONG desde ESP32 (OK)");
    }
  }
}
