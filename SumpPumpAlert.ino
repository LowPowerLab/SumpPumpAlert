/*
#  SumpPumpAlert based on Moteino, HC-SR04 ultrasonic sensor and RaspberryPi
#
#  Copyright (c) 2013 Felix Rusu (felix@lowpowerlab.com).  All rights reserved.
#
#  This code is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 2.1 of the License, or (at your option) any later version.
#
#  This code is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public
#  License along with this library; if not, write to the Free Software
#  Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
#
*/

#include <LowPower.h> // get this library at: http://www.rocketscream.com/blog/2011/07/04/lightweight-low-power-arduino-library/
#include <RFM12B.h>   // get ths library at: https://github.com/LowPowerLab/RFM12B

#define MYID         17        // node ID used for this unit
#define NETWORKID   100
#define GATEWAYID    1
#define FREQUENCY  RF12_433MHZ //Match this with the version of your Moteino! (others: RF12_433MHZ, RF12_915MHZ)
#define KEY  "ABCDABCDABCDABCD"
#define SERIAL_BAUD  115200
#define ACK_TIME    20
#define LED          9

#define ECHO 3
#define TRIG 4
RFM12B radio;
char sendBuf[32];

void setup() {
  Serial.begin(SERIAL_BAUD);
  pinMode(TRIG, OUTPUT);
  pinMode(ECHO, INPUT);
  radio.Initialize(MYID, FREQUENCY, NETWORKID);
  radio.Encrypt((byte*)KEY);
  radio.Sleep();
}

void loop() {
  long distance = getDistance();
  byte sendLen;
  
  if (distance >= 400 || distance <= 0){
    Serial.println("Out of range");
    sprintf(sendBuf, "SUMP_LVL:BAD");
  }
  else {
    Serial.print(distance);
    Serial.println(" cm");
    sprintf(sendBuf, "SUMP_LVL:%ld", distance);
  }
  Blink(LED, 50);
 
  sendLen = strlen(sendBuf);
  radio.Wakeup();
  radio.Send(GATEWAYID, sendBuf, sendLen, false);
  radio.Sleep();
  LowPower.powerDown(SLEEP_8S, ADC_OFF, BOD_OFF);
}

long getDistance() {
  long duration, distance;
  digitalWrite(TRIG, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG, LOW);
  duration = pulseIn(ECHO, HIGH);
  distance = (duration/2) / 29.1;
  return distance;
}

// wait a few milliseconds for proper ACK to me, return true if indeed received
static bool waitForAck() {
  long now = millis();
  while (millis() - now <= ACK_TIME) {
    if (radio.ACKReceived(GATEWAYID))
      return true;
  }
  return false;
}

void Blink(byte PIN, int DELAY_MS)
{
  pinMode(PIN, OUTPUT);
  digitalWrite(PIN,HIGH);
  delay(DELAY_MS);
  digitalWrite(PIN,LOW);
}
