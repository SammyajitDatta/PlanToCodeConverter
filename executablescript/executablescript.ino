#include <Arduino.h>

constexpr int PIR_PIN = 27;
constexpr int LED_PIN = 25;
constexpr int BUZZER_PIN = 33;

bool detectMotion() {
  return digitalRead(PIR_PIN) == HIGH;
}

void turnLEDOn() {
  digitalWrite(LED_PIN, HIGH);
}

void turnBuzzerOn() {
  digitalWrite(BUZZER_PIN, HIGH);
}

void turnLEDOff() {
  digitalWrite(LED_PIN, LOW);
}

void turnBuzzerOff() {
  digitalWrite(BUZZER_PIN, LOW);
}

void setup() {
  pinMode(PIR_PIN, INPUT);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, LOW);
}

void loop() {
  if (detectMotion()) {
    turnLEDOn();
    turnBuzzerOn();
    delay(500UL);
    turnLEDOff();
    turnBuzzerOff();
    delay(500UL);
  } else {
    turnLEDOff();
    turnBuzzerOff();
  }
}
