#pragma once
#include <ArduinoJson.h>
#include <cstring>
inline bool validAck(const char *body, size_t length, const char *expectedId) {
  if (!length || length > 512) return false;
  JsonDocument doc;
  if (deserializeJson(doc, body, length)) return false;
  const char *id = doc["batch_id"].as<const char *>();
  return doc["accepted"].is<bool>() && doc["accepted"].as<bool>() &&
         id != nullptr && std::strcmp(id, expectedId) == 0;
}
