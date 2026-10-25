#include <SoftwareSerial.h>
#include <DHT.h>  // Librería "DHT sensor library" de Adafruit (+ "Adafruit Unified Sensor")
#include <OneWire.h> //Librería "Sensor de Temperatura DS18B20"
#include <DallasTemperature.h>


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

  Serial.println("--- ARDUINO UNO LISTO ---");
}

void loop() {
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
