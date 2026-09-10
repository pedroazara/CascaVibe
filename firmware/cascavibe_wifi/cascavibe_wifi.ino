/*
 * CascaVibe 0.2.0 - acelerometro + Wi-Fi + cliente HTTP/S.
 * ESP32-WROOM-32 DevKit; SDA=21, SCL=22, VCC=3V3, AD0=GND.
 * Bibliotecas: Adafruit MPU6050, Unified Sensor, BusIO, ArduinoJson 7.
 * O gyro permanece como referencia interna de clock; so aceleracao vai ao FIFO.
 */
#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_MPU6050.h>
#include <ArduinoJson.h>
#include <WiFi.h>
#include <WebServer.h>
#include <DNSServer.h>
#include <Preferences.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <esp_timer.h>
#include <esp_system.h>
#include <time.h>
#include "protocol.h"
#include "ack.h"
#include "panel.h"

constexpr char VERSION[] = "0.2.0";
constexpr char LOCAL_USER[] = "cascavibe";
constexpr char LOCAL_PASSWORD[] = "cascavibe";
constexpr uint8_t FIFO_EN_REG = 0x23, USER_CTRL_REG = 0x6A;
constexpr uint8_t FIFO_COUNT_REG = 0x72, FIFO_DATA_REG = 0x74;
constexpr size_t QUEUE_DEPTH = 6;

struct Config {
  String ssid, password, apiUrl, apiToken, rootCa;
  bool allowHttp = false;
};
struct Status {
  bool sensorOk = false, hasBatch = false, inFlight = false;
  uint64_t samples = 0, lastSampleUs = 0;
  uint32_t batches = 0, dropped = 0, fifoOverflows = 0, readErrors = 0;
  uint32_t discardedPartial = 0, acknowledged = 0, lastAckSeq = 0;
  int httpCode = 0;
  float measuredHz = 0, ax = 0, ay = 0, az = 0;
  char delivery[100] = "API ainda nao configurada";
};
Config cfg;  // Imutavel entre boots: salvar configuracao reinicia o dispositivo.
Status stats;
cv::Batch latestBatch{};
SemaphoreHandle_t stateLock;
QueueHandle_t batches;
Preferences prefs;
WebServer server(80);
DNSServer dns;
Adafruit_MPU6050 mpu;
String deviceId, bootId, apName, csrf;
uint8_t sensorAddress = 0x68;
uint32_t rebootAt = 0;

String randomHex(size_t bytes) {
  String value;
  for (size_t i = 0; i < bytes; ++i) {
    char part[3];
    snprintf(part, sizeof(part), "%02x", unsigned(esp_random() & 255));
    value += part;
  }
  return value;
}
Status snapshot() {
  xSemaphoreTake(stateLock, portMAX_DELAY);
  Status copy = stats;
  xSemaphoreGive(stateLock);
  return copy;
}
void delivery(const char *message, int code = 0) {
  xSemaphoreTake(stateLock, portMAX_DELAY);
  snprintf(stats.delivery, sizeof(stats.delivery), "%s", message);
  stats.httpCode = code;
  xSemaphoreGive(stateLock);
}
bool regWrite(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(sensorAddress);
  Wire.write(reg); Wire.write(val);
  return Wire.endTransmission() == 0;
}
bool regRead(uint8_t reg, uint8_t *data, size_t n) {
  Wire.beginTransmission(sensorAddress);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(sensorAddress, n, true) != n) return false;
  for (size_t i = 0; i < n; ++i) data[i] = Wire.read();
  return true;
}
bool resetFifo() {
  if (!regWrite(FIFO_EN_REG, 0) || !regWrite(USER_CTRL_REG, 0x04)) return false;
  delay(2);
  return regWrite(USER_CTRL_REG, 0x40) && regWrite(FIFO_EN_REG, 0x08);
}
bool initSensor() {
  bool found = false;
  for (uint8_t address : {uint8_t(0x68), uint8_t(0x69)}) {
    if (mpu.begin(address, &Wire)) { sensorAddress = address; found = true; break; }
  }
  if (!found) return false;
  mpu.setAccelerometerRange(MPU6050_RANGE_2_G);
  mpu.setFilterBandwidth(MPU6050_BAND_94_HZ);
  mpu.setSampleRateDivisor(1);  // Base 1 kHz com DLPF ligado / (1+1) = 500 Hz.
  delay(50);
  uint8_t regs[4];
  // Ler de volta divisor, filtro, gyro config e accel config.
  if (!regRead(0x19, regs, 4) || regs[0] != 1 ||
      (regs[1] & 7) != 2 || (regs[3] & 0x18) != 0) return false;
  return resetFifo();
}
void publish(const cv::Batch &batch) {
  xSemaphoreTake(stateLock, portMAX_DELAY);
  latestBatch = batch;
  stats.hasBatch = true;
  ++stats.batches;
  xSemaphoreGive(stateLock);
  if (!cfg.apiUrl.isEmpty() && xQueueSend(batches, &batch, 0) != pdTRUE) {
    xSemaphoreTake(stateLock, portMAX_DELAY);
    ++stats.dropped;  // Descarta lote novo; nunca altera lote em tentativa de envio.
    xSemaphoreGive(stateLock);
  }
}
void acquisitionTask(void *) {
  cv::Assembler assembler;
  bool ready = false;
  uint64_t originUs = 0, total = 0, rateCount = 0;
  int64_t rateStart = esp_timer_get_time();
  for (;;) {
    if (!ready) {
      ready = initSensor();
      xSemaphoreTake(stateLock, portMAX_DELAY);
      stats.sensorOk = ready;
      xSemaphoreGive(stateLock);
      if (!ready) { vTaskDelay(pdMS_TO_TICKS(2000)); continue; }
      assembler.discontinuity();
      originUs = 0;
      rateStart = esp_timer_get_time(); rateCount = 0;
    }
    uint8_t countBytes[2], interruptStatus;
    bool ok = regRead(0x3A, &interruptStatus, 1) && regRead(FIFO_COUNT_REG, countBytes, 2);
    uint16_t count = ok ? (uint16_t(countBytes[0]) << 8) | countBytes[1] : 0;
    bool overflow = ok && ((interruptStatus & 0x10) || count >= 1024);
    if (!ok || overflow) {
      xSemaphoreTake(stateLock, portMAX_DELAY);
      if (overflow) ++stats.fifoOverflows; else ++stats.readErrors;
      stats.discardedPartial += assembler.used;
      stats.sensorOk = false; stats.measuredHz = 0;
      xSemaphoreGive(stateLock);
      ready = false;
      continue;
    }
    // FIFO de aceleracao: 6 bytes big-endian por amostra, sem gyro/temperatura.
    size_t frames = count / 6;
    if (frames && !originUs) {
      originUs = uint64_t(esp_timer_get_time()) - (frames - 1) * cv::PeriodUs;
    }
    while (frames) {
      const size_t take = frames > 16 ? 16 : frames;
      uint8_t raw[96];
      if (!regRead(FIFO_DATA_REG, raw, take * 6)) {
        xSemaphoreTake(stateLock, portMAX_DELAY);
        ++stats.readErrors; stats.discardedPartial += assembler.used;
        stats.sensorOk = false;
        xSemaphoreGive(stateLock);
        ready = false;
        break;
      }
      for (size_t i = 0; i < take; ++i) {
        cv::Sample s = cv::decode(raw + i * 6);
        uint64_t timestamp = originUs + assembler.nextIndex * cv::PeriodUs;
        if (assembler.push(s, timestamp)) publish(assembler.batch);
        ++total; ++rateCount;
      }
      cv::Sample last = cv::decode(raw + (take - 1) * 6);
      xSemaphoreTake(stateLock, portMAX_DELAY);
      stats.samples = total; stats.lastSampleUs = esp_timer_get_time();
      stats.ax = last.x * cv::Scale; stats.ay = last.y * cv::Scale; stats.az = last.z * cv::Scale;
      xSemaphoreGive(stateLock);
      frames -= take;
    }
    int64_t now = esp_timer_get_time();
    if (now - rateStart >= 1000000) {
      xSemaphoreTake(stateLock, portMAX_DELAY);
      stats.measuredHz = rateCount * 1000000.0 / (now - rateStart);
      xSemaphoreGive(stateLock);
      rateCount = 0; rateStart = now;
    }
    vTaskDelay(pdMS_TO_TICKS(4));  // FIFO desacopla amostragem do agendamento.
  }
}
String batchId(const cv::Batch &b) {
  return deviceId + "." + bootId + "." + String(b.seq);
}
String encodeBatch(const cv::Batch &b) {
  String out;
  if (!out.reserve(15000)) return String();
  JsonDocument meta;
  meta["schema_version"] = 1;
  meta["device_id"] = deviceId;
  meta["boot_id"] = bootId;
  meta["batch_id"] = batchId(b);
  meta["batch_seq"] = b.seq;
  meta["segment_id"] = b.segment;
  meta["firmware_version"] = VERSION;
  meta["sensor"] = "MPU6050";
  meta["sample_rate_hz"] = cv::RateHz;
  meta["sample_count"] = cv::SamplesPerBatch;
  meta["first_sample_index"] = b.firstIndex;
  meta["t0_monotonic_us"] = b.t0Us;
  meta["timestamp_quality"] = "estimated_from_fifo";
  meta["dt_us"] = cv::PeriodUs;
  meta["encoding"] = "int16_counts";
  meta["accel_range_g"] = 2;
  meta["dlpf_cfg"] = 2;
  meta["scale_m_s2_per_count"] = double(9.80665 / 16384.0);
  meta["clipped_samples"] = b.clippedCount;
  meta["t0_utc"] = nullptr;  // NTP serve ao TLS; nao inventamos tempo UTC de captura.
  JsonArray axes = meta["axis_order"].to<JsonArray>();
  axes.add("x"); axes.add("y"); axes.add("z");
  serializeJson(meta, out);
  out.remove(out.length() - 1);
  out += ",\"samples\":[";
  char row[28];
  for (size_t i = 0; i < cv::SamplesPerBatch; ++i) {
    auto s = b.samples[i];
    snprintf(row, sizeof(row), "%s[%d,%d,%d]", i ? "," : "", int(s.x), int(s.y), int(s.z));
    out += row;
  }
  out += "]}";
  return out;
}
bool postBatch(const cv::Batch &b, bool &permanentError) {
  HTTPClient http;
  WiFiClient plain;
  WiFiClientSecure secure;
  bool tls = cfg.apiUrl.startsWith("https://");
  if (tls && time(nullptr) < 1700000000) {
    delivery("HTTPS aguardando sincronizacao do relogio");
    return false;
  }
  if (tls) {
    secure.setCACert(cfg.rootCa.c_str());
    secure.setHandshakeTimeout(5);
  }
  bool begun = tls ? http.begin(secure, cfg.apiUrl) : http.begin(plain, cfg.apiUrl);
  if (!begun) { delivery("Nao foi possivel iniciar cliente HTTP"); return false; }
  http.setConnectTimeout(3000);
  http.setTimeout(3000);
  http.setFollowRedirects(HTTPC_DISABLE_FOLLOW_REDIRECTS);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Authorization", "Bearer " + cfg.apiToken);
  http.addHeader("Idempotency-Key", batchId(b));
  String payload = encodeBatch(b);
  if (payload.isEmpty()) { http.end(); delivery("Sem memoria para serializar lote"); return false; }
  const int code = http.POST(payload);
  bool ack = false;
  if ((code == 200 || code == 201) && http.getSize() > 0 && http.getSize() <= 512) {
    // Contrato exige Content-Length; impede resposta arbitrariamente grande.
    const size_t length = http.getSize();
    char answer[513] = {};
    auto *stream = http.getStreamPtr();
    stream->setTimeout(3000);
    size_t received = stream->readBytes(answer, length);
    if (received == length) ack = validAck(answer, received, batchId(b).c_str());
  }
  http.end();
  if (ack) {
    xSemaphoreTake(stateLock, portMAX_DELAY);
    ++stats.acknowledged; stats.lastAckSeq = b.seq;
    xSemaphoreGive(stateLock);
    delivery("Lote confirmado pela API", code);
    return true;
  }
  permanentError = code >= 400 && code < 500 && code != 408 && code != 429;
  delivery(permanentError ? "API rejeitou lote: corrija configuracao/servidor e reinicie" :
           ((code == 200 || code == 201) ? "ACK invalido: confira corpo e Content-Length" :
            "Sem confirmacao da API; repeticao automatica"), code);
  return false;
}
void senderTask(void *) {
  cv::Batch batch;
  for (;;) {
    if (xQueueReceive(batches, &batch, portMAX_DELAY) != pdTRUE) continue;
    xSemaphoreTake(stateLock, portMAX_DELAY); stats.inFlight = true; xSemaphoreGive(stateLock);
    uint32_t backoff = 1000;
    bool permanentError = false;
    for (;;) {
      if (permanentError) { vTaskDelay(pdMS_TO_TICKS(1000)); continue; }
      if (WiFi.status() != WL_CONNECTED) {
        delivery("Aguardando Wi-Fi; lotes em memoria");
        vTaskDelay(pdMS_TO_TICKS(1000));
        continue;
      }
      if (postBatch(batch, permanentError)) break;
      vTaskDelay(pdMS_TO_TICKS(backoff + esp_random() % 500));
      backoff = backoff < 30000 ? backoff * 2 : 60000;
    }
    xSemaphoreTake(stateLock, portMAX_DELAY); stats.inFlight = false; xSemaphoreGive(stateLock);
  }
}
void loadConfig() {
  prefs.begin("cascavibe", false);
  JsonDocument doc;
  deserializeJson(doc, prefs.getString("config", "{}"));
  cfg.ssid = doc["ssid"] | ""; cfg.password = doc["password"] | "";
  cfg.apiUrl = doc["api_url"] | ""; cfg.apiToken = doc["api_token"] | "";
  cfg.rootCa = doc["root_ca"] | ""; cfg.allowHttp = doc["allow_http"] | false;
}
bool authorized(bool mutate = false) {
  if (!server.authenticate(LOCAL_USER, LOCAL_PASSWORD)) {
    server.requestAuthentication();
    return false;
  }
  if (mutate && server.header("X-CV-CSRF") != csrf) {
    server.send(403, "application/json", "{\"error\":\"Recarregue a pagina antes de salvar\"}");
    return false;
  }
  server.sendHeader("Cache-Control", "no-store");
  server.sendHeader("X-Content-Type-Options", "nosniff");
  return true;
}
void sendJson(JsonDocument &doc, int code = 200) {
  String output; serializeJson(doc, output);
  server.send(code, "application/json", output);
}
void handleStatus() {
  if (!authorized()) return;
  Status s = snapshot();
  JsonDocument d;
  d["device_id"] = deviceId; d["firmware"] = VERSION;
  d["csrf"] = csrf; d["ap_name"] = apName;
  d["wifi_connected"] = WiFi.status() == WL_CONNECTED;
  d["wifi_ssid"] = cfg.ssid; d["ip"] = WiFi.localIP().toString();
  d["open_network"] = !cfg.ssid.isEmpty() && cfg.password.isEmpty();
  d["rssi"] = WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : 0;
  d["sensor_ok"] = s.sensorOk && s.lastSampleUs && esp_timer_get_time() - s.lastSampleUs < 1000000;
  d["sample_rate_hz"] = cv::RateHz; d["measured_hz"] = s.measuredHz;
  d["samples"] = s.samples; d["batches"] = s.batches;
  d["queue"] = uxQueueMessagesWaiting(batches) + (s.inFlight ? 1 : 0);
  d["dropped_batches"] = s.dropped; d["overflows"] = s.fifoOverflows;
  d["read_errors"] = s.readErrors; d["discarded_partial_samples"] = s.discardedPartial;
  d["acknowledged"] = s.acknowledged; d["last_ack_seq"] = s.lastAckSeq;
  d["http_code"] = s.httpCode; d["delivery"] = s.delivery;
  d["ax"] = s.ax; d["ay"] = s.ay; d["az"] = s.az;
  d["api_url"] = cfg.apiUrl; d["allow_http"] = cfg.allowHttp;
  d["has_token"] = !cfg.apiToken.isEmpty(); d["has_ca"] = !cfg.rootCa.isEmpty();
  d["free_heap"] = ESP.getFreeHeap(); d["has_batch"] = s.hasBatch;
  sendJson(d);
}
bool httpLocal(const String &url) {
  // HTTP sem TLS somente em IP privado literal: laboratorio, sem redirecionamentos.
  int start = url.indexOf("://") + 3;
  int slash = url.indexOf('/', start);
  String authority = slash < 0 ? url.substring(start) : url.substring(start, slash);
  int colon = authority.indexOf(':');
  String host = colon < 0 ? authority : authority.substring(0, colon);
  IPAddress ip;
  if (!ip.fromString(host)) return false;
  return ip[0] == 10 || (ip[0] == 172 && ip[1] >= 16 && ip[1] <= 31) ||
         (ip[0] == 192 && ip[1] == 168);
}
void handleConfig() {
  if (!authorized(true)) return;
  if (server.arg("plain").length() > 10000) { server.send(413); return; }
  JsonDocument d;
  if (deserializeJson(d, server.arg("plain"))) { server.send(400); return; }
  Config next = cfg;
  next.ssid = d["ssid"] | "";
  String pass = d["password"] | "";
  if (d["open_network"] | false) next.password = "";
  else if (!pass.isEmpty()) next.password = pass;
  else if (next.ssid != cfg.ssid) next.password = "";
  next.apiUrl = d["api_url"] | "";
  next.apiUrl.trim();
  next.allowHttp = d["allow_http"] | false;
  String token = d["api_token"] | "";
  String ca = d["root_ca"] | "";
  if (!token.isEmpty()) next.apiToken = token;
  if (!ca.isEmpty()) next.rootCa = ca;
  if (d["clear_token"] | false) next.apiToken = "";
  if (d["clear_ca"] | false) next.rootCa = "";
  String error;
  if (next.ssid.isEmpty() || next.ssid.length() > 32) error = "SSID deve ter 1 a 32 bytes";
  else if (!(d["open_network"] | false) && (next.password.length() < 8 || next.password.length() > 63))
    error = "Senha Wi-Fi: 8 a 63 caracteres, ou selecione rede aberta";
  else if (next.apiToken.length() > 256 || next.apiToken.indexOf('\r') >= 0 || next.apiToken.indexOf('\n') >= 0)
    error = "Token invalido";
  else if (next.apiUrl.length() > 256 || next.rootCa.length() > 6000) error = "URL ou CA muito grande";
  else if (next.apiUrl.indexOf('@') >= 0 || next.apiUrl.indexOf('#') >= 0 ||
           next.apiUrl.indexOf(' ') >= 0 || next.apiUrl.indexOf('\r') >= 0 || next.apiUrl.indexOf('\n') >= 0)
    error = "URL invalida: nao inclua credenciais, fragmentos ou espacos";
  else if (!next.apiUrl.isEmpty()) {
    if (next.apiToken.isEmpty()) error = "Configure um token para a API";
    else if (next.apiUrl.startsWith("https://")) {
      if (next.rootCa.indexOf("-----BEGIN CERTIFICATE-----") < 0 ||
          next.rootCa.indexOf("-----END CERTIFICATE-----") < 0) error = "HTTPS exige CA PEM";
    } else if (!(next.apiUrl.startsWith("http://") && next.allowHttp && httpLocal(next.apiUrl)))
      error = "Use HTTPS ou habilite HTTP de laboratorio com IP privado literal";
  }
  if (!error.isEmpty()) { JsonDocument e; e["error"] = error; sendJson(e, 400); return; }
  JsonDocument saved;
  saved["ssid"] = next.ssid; saved["password"] = next.password;
  saved["api_url"] = next.apiUrl; saved["api_token"] = next.apiToken;
  saved["root_ca"] = next.rootCa; saved["allow_http"] = next.allowHttp;
  String storage; serializeJson(saved, storage);
  if (prefs.putString("config", storage) != storage.length()) {
    server.send(500, "application/json", "{\"error\":\"Falha ao salvar configuracao\"}"); return;
  }
  server.send(200, "application/json", "{\"saved\":true,\"reboot_in_seconds\":2}");
  rebootAt = millis() + 2000;
}
void handleNetworks() {
  if (!authorized()) return;
  int found = WiFi.scanComplete();
  JsonDocument d;
  if (found == WIFI_SCAN_RUNNING) { d["scanning"] = true; sendJson(d); return; }
  if (found < 0) { WiFi.scanNetworks(true); d["scanning"] = true; sendJson(d); return; }
  d["scanning"] = false;
  JsonArray list = d["networks"].to<JsonArray>();
  for (int i = 0; i < found && i < 24; ++i) {
    JsonObject item = list.add<JsonObject>();
    item["ssid"] = WiFi.SSID(i); item["rssi"] = WiFi.RSSI(i);
    item["open"] = WiFi.encryptionType(i) == WIFI_AUTH_OPEN;
  }
  WiFi.scanDelete();
  sendJson(d);
}
void setup() {
  Serial.begin(115200);
  stateLock = xSemaphoreCreateMutex();
  batches = xQueueCreate(QUEUE_DEPTH, sizeof(cv::Batch));
  if (!stateLock || !batches) { Serial.println("ERROR: memoria insuficiente"); while (true) delay(1000); }
  uint64_t mac = ESP.getEfuseMac();
  char id[20];
  snprintf(id, sizeof(id), "cv-%012llx", (unsigned long long)mac);
  deviceId = id; bootId = randomHex(16); csrf = randomHex(16);
  loadConfig();
  apName = "CascaVibe-" + deviceId.substring(deviceId.length() - 6);
  Wire.begin(21, 22); Wire.setClock(400000); Wire.setTimeOut(30);
  WiFi.persistent(false);
  WiFi.setHostname(deviceId.c_str());
  WiFi.mode(WIFI_AP_STA);
  WiFi.setAutoReconnect(true);
  WiFi.softAP(apName.c_str(), LOCAL_PASSWORD, 1, false, 2);
  dns.start(53, "*", WiFi.softAPIP());
  if (!cfg.ssid.isEmpty()) WiFi.begin(cfg.ssid.c_str(), cfg.password.c_str());
  configTime(0, 0, "pool.ntp.org", "time.google.com");
  // Credencial local de instalacao; nunca imprime senha Wi-Fi do local ou token da API.
  Serial.printf("\nCascaVibe %s\nRede: %s\nSenha AP/painel: %s\nUsuario: %s\nAbrir http://192.168.4.1\n",
                VERSION, apName.c_str(), LOCAL_PASSWORD, LOCAL_USER);
  const char *headers[] = {"X-CV-CSRF"};
  server.collectHeaders(headers, 1);
  server.on("/", HTTP_GET, []() {
    if (!authorized()) return;
    server.sendHeader("X-Frame-Options", "DENY");
    server.send_P(200, "text/html; charset=utf-8", PANEL_HTML);
  });
  server.on("/api/status", HTTP_GET, handleStatus);
  server.on("/api/networks", HTTP_GET, handleNetworks);
  server.on("/api/config", HTTP_POST, handleConfig);
  server.on("/api/batch", HTTP_GET, []() {
    if (!authorized()) return;
    cv::Batch b;
    xSemaphoreTake(stateLock, portMAX_DELAY);
    bool available = stats.hasBatch; b = latestBatch;
    xSemaphoreGive(stateLock);
    if (!available) { server.send(503, "application/json", "{\"error\":\"Nenhum lote completo\"}"); return; }
    server.sendHeader("Content-Disposition", "attachment; filename=cascavibe-lote.json");
    server.send(200, "application/json", encodeBatch(b));
  });
  server.on("/api/reboot", HTTP_POST, []() {
    if (!authorized(true)) return;
    server.send(200, "application/json", "{\"restarting\":true}");
    rebootAt = millis() + 1500;
  });
  server.onNotFound([]() {
    server.sendHeader("Location", "http://192.168.4.1/", true);
    server.send(302, "text/plain", "Abra http://192.168.4.1");
  });
  server.begin();
  if (xTaskCreatePinnedToCore(acquisitionTask, "acquisition", 8192, nullptr, 3, nullptr, 1) != pdPASS ||
      xTaskCreatePinnedToCore(senderTask, "sender", 12288, nullptr, 1, nullptr, 0) != pdPASS) {
    Serial.println("ERROR: nao foi possivel iniciar as tarefas");
    while (true) delay(1000);
  }
}
void loop() {
  dns.processNextRequest();
  server.handleClient();
  if (rebootAt && int32_t(millis() - rebootAt) >= 0) ESP.restart();
  static uint32_t retryAt = 0;
  if (!cfg.ssid.isEmpty() && WiFi.status() != WL_CONNECTED && millis() - retryAt > 30000) {
    retryAt = millis();
    WiFi.reconnect();
  }
  delay(2);
}
