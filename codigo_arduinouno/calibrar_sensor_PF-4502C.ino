#define PIN_PH A1 //Salida PO del módulo
#define MUESTRAS 20 //Promedio para estabilizar

//Voltajes al momento de calibrar el sensor
float voltaje_pH7=2.50;
float voltaje_pH4=3.04;

void setup(){
    Serial.begin(9600);
    pinMode(PIN_PH, INPUT);
}

float leerVoltaje(){
    long s = 0;
    for(int i=0; i < MUESTRAS; i++){
        s+=analogRead(PIN_PH);
        delay(10);
    }
    return (s/(float)MUESTRAS)*5.0/1023.0;
}

void loop(){
    float v = leerVoltaje();
    float m = (7.0-4.0)/(voltaje_pH7-voltaje_pH4);
    float pH = m*v + (7.0-m*voltaje_pH7)
    serial.print("Voltaje: ");
    serial.print(v,3);
    serial.print(" V  pH: ");
    serial.println(pH,2);
    delay(1000);
}

