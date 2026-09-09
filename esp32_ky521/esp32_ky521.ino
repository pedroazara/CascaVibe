/*
  CascaVibe — etapa 1: KY-521 (MPU-6050) com ESP32

  Bibliotecas da Arduino IDE (Gerenciar Bibliotecas):
    - Adafruit MPU6050
    - Adafruit Unified Sensor
    - Adafruit BusIO

  Saída USB serial (50 Hz):
  IMU,ms,roll,pitch,yaw,ax,ay,az,gx,gy,gz,temp
  Ângulos: graus; aceleração: m/s² (inclui gravidade); giro: °/s.
  Yaw é relativo à partida, NÃO é norte magnético.
  Mantenha o sensor parado durante a calibração inicial de 2 segundos.
*/

#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <Wire.h>
#include <math.h>

constexpr int SDA_PIN = 21;
constexpr int SCL_PIN = 22;
constexpr uint32_t SERIAL_BAUD = 115200;
constexpr float RAD_TO_DEGREE = 57.2957795f;

Adafruit_MPU6050 mpu;
float roll = 0.0f;
float pitch = 0.0f;
float yaw = 0.0f;
float biasX = 0.0f, biasY = 0.0f, biasZ = 0.0f;
unsigned long previousMicros = 0;

float wrapAngle(float angle) {
  return fmodf(angle + 540.0f, 360.0f) - 180.0f;
}

bool calibrateGyro() {
  Serial.println("INFO,CALIBRATING: mantenha o sensor parado por 2 s");
  float sums[3] = {}, squares[3] = {};
  bool stationary = true;
  constexpr int samples = 200;
  for (int i = 0; i < samples; ++i) {
    sensors_event_t a, g, t;
    if (!mpu.getEvent(&a, &g, &t)) return false;
    const float v[] = {g.gyro.x, g.gyro.y, g.gyro.z};
    const float norm = sqrtf(a.acceleration.x * a.acceleration.x +
                             a.acceleration.y * a.acceleration.y +
                             a.acceleration.z * a.acceleration.z);
    if (fabsf(norm - 9.80665f) > 1.0f) stationary = false;
    for (int k = 0; k < 3; ++k) {
      sums[k] += v[k];
      squares[k] += v[k] * v[k];
      if (fabsf(v[k]) > 0.15f) stationary = false;
    }
    delay(10);
  }
  for (int k = 0; k < 3; ++k) {
    const float mean = sums[k] / samples;
    if (squares[k] / samples - mean * mean > 0.0004f) stationary = false;
  }
  if (!stationary) {
    Serial.println("INFO,MOVIMENTO: repetindo calibracao, deixe o sensor parado");
    return false;
  }
  biasX = sums[0] / samples;
  biasY = sums[1] / samples;
  biasZ = sums[2] / samples;
  return true;
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  Wire.begin(SDA_PIN, SCL_PIN);

  if (!mpu.begin(0x68, &Wire) && !mpu.begin(0x69, &Wire)) {
    while (true) {
      Serial.println("ERROR,MPU6050_NOT_FOUND: confira alimentacao e I2C");
      delay(1000);
    }
  }

  mpu.setAccelerometerRange(MPU6050_RANGE_2_G);
  mpu.setGyroRange(MPU6050_RANGE_250_DEG);
  mpu.setFilterBandwidth(MPU6050_BAND_21_HZ);
  delay(100);
  while (!calibrateGyro()) delay(250);

  sensors_event_t accel, gyro, temp;
  if (!mpu.getEvent(&accel, &gyro, &temp)) {
    Serial.println("ERROR,MPU6050_READ_FAILED");
    delay(100);
    return;
  }
  roll = atan2f(accel.acceleration.y, accel.acceleration.z) * RAD_TO_DEGREE;
  pitch = atan2f(-accel.acceleration.x,
                 sqrtf(accel.acceleration.y * accel.acceleration.y +
                       accel.acceleration.z * accel.acceleration.z)) * RAD_TO_DEGREE;
  previousMicros = micros();
  Serial.println("INFO,CascaVibe KY-521 pronto");
}

void loop() {
  sensors_event_t accel, gyro, temp;
  if (!mpu.getEvent(&accel, &gyro, &temp)) {
    Serial.println("ERROR,MPU6050_READ_FAILED");
    delay(100);
    return;
  }

  const unsigned long now = micros();
  const float dt = fminf((now - previousMicros) / 1000000.0f, 0.1f);
  previousMicros = now;

  const float accelRoll = atan2f(accel.acceleration.y, accel.acceleration.z) * RAD_TO_DEGREE;
  const float accelPitch = atan2f(-accel.acceleration.x,
                                  sqrtf(accel.acceleration.y * accel.acceleration.y +
                                        accel.acceleration.z * accel.acceleration.z)) * RAD_TO_DEGREE;

  // A biblioteca fornece o giro em rad/s; o filtro complementar opera em graus.
  const float gyroX = (gyro.gyro.x - biasX) * RAD_TO_DEGREE;
  const float gyroY = (gyro.gyro.y - biasY) * RAD_TO_DEGREE;
  const float gyroZ = (gyro.gyro.z - biasZ) * RAD_TO_DEGREE;
  const float r = roll / RAD_TO_DEGREE;
  const float p = pitch / RAD_TO_DEGREE;
  // Cinemática de Euler; perto da vertical, suspende yaw para evitar singularidade.
  const bool yawValid = fabsf(pitch) < 80.0f;
  const float cross = gyroY * sinf(r) + gyroZ * cosf(r);
  const float rollRate = gyroX + (yawValid ? tanf(p) * cross : 0.0f);
  const float pitchRate = gyroY * cosf(r) - gyroZ * sinf(r);
  if (yawValid) yaw = fmodf(yaw + cross / cosf(p) * dt + 360.0f, 360.0f);
  const float alpha = 0.5f / (0.5f + dt);
  roll = wrapAngle(roll + rollRate * dt);
  roll = wrapAngle(roll + (1.0f - alpha) * wrapAngle(accelRoll - roll));
  pitch = alpha * (pitch + pitchRate * dt) + (1.0f - alpha) * accelPitch;

  Serial.printf("IMU,%lu,%.2f,%.2f,%.2f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.2f\n",
                millis(), roll, pitch, yaw,
                accel.acceleration.x, accel.acceleration.y, accel.acceleration.z,
                gyroX, gyroY, gyroZ, temp.temperature);
  const uint32_t elapsed = micros() - now;
  if (elapsed < 20000) delayMicroseconds(20000 - elapsed);
}
