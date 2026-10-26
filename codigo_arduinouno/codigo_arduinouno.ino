#include <SoftwareSerial.h>
#include <DHT.h>  // Librería "DHT sensor library" de Adafruit (+ "Adafruit Unified Sensor")
#include <OneWire.h> //Librería "Sensor de Temperatura DS18B20"
#include <DallasTemperature.h>
#include <EEPROM.h>  // guarda la calibración del pH para que sobreviva a reinicios


// Comunicación con el ESP32
// RX = Pin 10 (Recibe desde TX2 del ESP32)
// TX = Pin 11 (Envía hacia el divisor de voltaje / RX2 del ESP32)
SoftwareSerial espSerial(10, 11);

// Sensor de humedad suelo LM393
#define PIN_HUMSUELO_A0 A0   // Salida analógica (A0 del módulo)
#define PIN_HUMSUELO_D0 2    // Salida digital (D0 del módulo)


// Sensor DHT11: temperatura y humedad relativa del ambiente
#define PIN_DHT 3
DHT dht(PIN_DHT, DHT11);

//Sensor tierra Sensor de Temperatura DS18B20
#define ONE_WIRE_BUS 12      // Pin digital Sensor DS18B20 (con resistencia de 4.7k entre DATA y VCC)
OneWire oneWire(ONE_WIRE_BUS);
DallasTemperature sensorSuelo(&oneWire);

// Sensor de pH PH-4502C: salida analógica PO en A1.
// Ideal en el lombricultivo: pH 6.8 - 7.2 (neutro).
#define PIN_PH A1
const int MUESTRAS_PH = 20;          // promedio por lectura (20 x 10 ms)
const int MUESTRAS_PH_CAL = 50;      // promedio al guardar un punto de calibración

// Voltajes medidos en los buffers pH 7.0 y pH 4.0 (por defecto, hasta calibrar).
// Se guardan en EEPROM y se calibran por Monitor Serial (CAL7 / CAL4) o desde la web.
float voltaje_pH7 = 2.50;
float voltaje_pH4 = 3.04;
byte calibrados = 0;                 // bit0 = pH7 guardado, bit1 = pH4 guardado

struct CalibracionPH {
  uint16_t magia;
  float v7;
  float v4;
  byte flags;
};
const uint16_t MAGIA_EEPROM = 0xCA1B;

String bufferUsb = "";
String bufferEsp = "";

const unsigned long INTERVALO_ENVIO = 2000;  // ms entre lecturas
const int MUESTRAS = 10;                     // lecturas que se promedian

unsigned long ultimoEnvio = 0;

// --- Prueba de conexión PING/PONG (desactivada) ---
// unsigned long ultimoPing = 0;
// int contadorMensajes = 0;

// Promediamos varias muestras para eliminar el ruido eléctrico
int leerHumedadCruda() {
  long suma = 0;
  for (int i = 0; i < MUESTRAS; i++) {
    suma += analogRead(PIN_HUMSUELO_A0);
    delay(5);
  }
  return suma / MUESTRAS;
}

float leerVoltajePH(int muestras) {
  long suma = 0;
  for (int i = 0; i < muestras; i++) {
    suma += analogRead(PIN_PH);
    delay(10);
  }
  return (suma / (float)muestras) * 5.0 / 1023.0;
}

// Recta pH = m*V + b que pasa por (voltaje_pH7, 7.0) y (voltaje_pH4, 4.0)
float voltajeAPH(float v) {
  float m = (7.0 - 4.0) / (voltaje_pH7 - voltaje_pH4);
  float ph = m * v + (7.0 - m * voltaje_pH7);
  return constrain(ph, 0.0, 14.0);
}

void cargarCalibracion() {
  CalibracionPH c;
  EEPROM.get(0, c);
  if (c.magia == MAGIA_EEPROM && c.v7 > 0 && c.v7 < 5 && c.v4 > 0 && c.v4 < 5) {
    voltaje_pH7 = c.v7;
    voltaje_pH4 = c.v4;
    calibrados = c.flags & 3;
  }
}

void guardarCalibracion() {
  CalibracionPH c = {MAGIA_EEPROM, voltaje_pH7, voltaje_pH4, calibrados};
  EEPROM.put(0, c);
}

// Formato: CAL,<voltaje_pH7>,<voltaje_pH4>,<flags 0-3>  (flags=3: ambos puntos calibrados)
void enviarCalibracion() {
  String cal = "CAL," + String(voltaje_pH7, 3) + "," + String(voltaje_pH4, 3) + "," + String(calibrados);
  espSerial.println(cal);
  Serial.println(cal);
}

// Comandos (por Monitor Serial o desde la web vía ESP32):
//   CAL7 = guarda el voltaje actual como pH 7.0 | CAL4 = lo guarda como pH 4.0
//   CALRESET = vuelve a los valores por defecto | CAL = muestra la calibración
void procesarComando(String cmd) {
  cmd.trim();
  cmd.toUpperCase();
  if (cmd == "CAL7" || cmd == "CAL4") {
    float v = leerVoltajePH(MUESTRAS_PH_CAL);
    bool es7 = (cmd == "CAL7");
    float otro = es7 ? voltaje_pH4 : voltaje_pH7;
    bool otroListo = es7 ? (calibrados & 2) : (calibrados & 1);
    if (v < 0.5 || v > 4.5) {
      Serial.println("CAL rechazada: voltaje fuera de 0.5-4.5 V (revisa el cableado)");
      return;
    }
    if (otroListo && fabs(v - otro) < 0.1) {
      Serial.println("CAL rechazada: los dos buffers dieron casi el mismo voltaje");
      return;
    }
    if (es7) { voltaje_pH7 = v; calibrados |= 1; } else { voltaje_pH4 = v; calibrados |= 2; }
    guardarCalibracion();
    Serial.print(es7 ? "pH 7.0 guardado: " : "pH 4.0 guardado: ");
    Serial.print(v, 3);
    Serial.println(" V");
    enviarCalibracion();
    String ack = es7 ? "ACK,7" : "ACK,4";
    espSerial.println(ack);
    Serial.println(ack);
  } else if (cmd == "CALRESET") {
    voltaje_pH7 = 2.50;
    voltaje_pH4 = 3.04;
    calibrados = 0;
    guardarCalibracion();
    Serial.println("Calibración restablecida a valores por defecto");
    enviarCalibracion();
  } else if (cmd == "CAL") {
    enviarCalibracion();
  }
}

// Acumula caracteres sin bloquear y procesa cada línea completa
void leerComandos(Stream& fuente, String& buffer) {
  while (fuente.available() > 0) {
    char c = fuente.read();
    if (c == 0x0A) {  // fin de línea
      procesarComando(buffer);
      buffer = "";
    } else if (c != 0x0D && buffer.length() < 20) {  // ignora el retorno de carro
      buffer += c;
    }
  }
}

void setup() {
  Serial.begin(9600); //Comunicación entre el PC y el Arduino UNO
  espSerial.begin(9600); //Comunicación entre el Arduino UNO y el ESP32
  pinMode(PIN_HUMSUELO_D0, INPUT); 
  dht.begin();

  // La conversión del DS18B20 tarda hasta 750 ms; se pide al final de cada ciclo
  // y se lee en el siguiente, así el loop no se queda esperando.
  sensorSuelo.begin();
  sensorSuelo.setWaitForConversion(false);
  sensorSuelo.requestTemperatures();

  pinMode(PIN_PH, INPUT);
  cargarCalibracion();

  Serial.println("--- ARDUINO UNO LISTO ---");
  Serial.println("Calibrar pH: escribe CAL7 (buffer 7.0), CAL4 (buffer 4.0), CAL o CALRESET");
}

void loop() {
  // Atiende comandos del Monitor Serial (USB) y de la web (vía ESP32)
  leerComandos(Serial, bufferUsb);
  leerComandos(espSerial, bufferEsp);

  if (millis() - ultimoEnvio >= INTERVALO_ENVIO) {
    ultimoEnvio = millis();

    int cruda = leerHumedadCruda();
    int digital = digitalRead(PIN_HUMSUELO_D0);

    // Ambiente (DHT11). Se envía antes que HUM para que el servidor
    // los junte en la misma fila. Formato: AMB,<temp_C>,<humedad_relativa_%>
    float tempAmb = dht.readTemperature(); //5
    float humAmb = dht.readHumidity();
    if (!isnan(tempAmb) && !isnan(humAmb)) {
      String amb = "AMB," + String(tempAmb, 1) + "," + String(humAmb, 1);
      espSerial.println(amb);
      Serial.println(amb);
    } else {
      Serial.println("DHT11: error de lectura");  // no se envía al ESP32
    }

    // Temperatura del sustrato (DS18B20). Formato: SUE,<temp_C>
    // -127 = sensor desconectado; 85.0 = valor de arranque sin conversión válida.
    float tempSuelo = sensorSuelo.getTempCByIndex(0);
    sensorSuelo.requestTemperatures();  // la lectura queda lista para el próximo ciclo
    if (tempSuelo != DEVICE_DISCONNECTED_C && tempSuelo != 85.0) {
      String sue = "SUE," + String(tempSuelo, 1);
      espSerial.println(sue);
      Serial.println(sue);
    } else {
      Serial.println("DS18B20: error de lectura (revisa pin 12 y resistencia de 4.7k)");
    }

    // pH. Se envía antes que HUM para unirse a la misma fila.
    // Formato: PH,<pH>,<voltaje_V>
    float vPH = leerVoltajePH(MUESTRAS_PH);
    String ph = "PH," + String(voltajeAPH(vPH), 2) + "," + String(vPH, 3);
    espSerial.println(ph);
    Serial.println(ph);
    enviarCalibracion();

    // Formato: HUM,<valor_analogico_0-1023>,<estado_digital_0/1>
    String linea = "HUM," + String(cruda) + "," + String(digital);
    espSerial.println(linea);  // hacia el ESP32
    Serial.println(linea);     // también por USB (útil para depurar)
  }

  // --- Prueba de conexión PING/PONG (desactivada) ---
  // if (millis() - ultimoPing >= 3000) {
  //   ultimoPing = millis();
  //   contadorMensajes++;
  //   espSerial.println("PING #" + String(contadorMensajes) + " desde Arduino");
  // }
  // while (espSerial.available() > 0) {
  //   String respuesta = espSerial.readStringUntil('\n');
  //   respuesta.trim();
  //   if (respuesta.length() > 0) {
  //     Serial.print("[ESP32 -> Arduino]: ");
  //     Serial.println(respuesta);
  //   }
  // }
}
