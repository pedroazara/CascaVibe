#include "../cascavibe_wifi/protocol.h"
#include "../cascavibe_wifi/ack.h"
#include <cassert>
#include <cmath>
#include <iostream>

int main() {
  const uint8_t raw[] = {0x80,0, 0x7f,0xff, 0xc0,0};
  const auto s = cv::decode(raw);
  assert(s.x == -32768 && s.y == 32767 && s.z == -16384);
  assert(std::abs(s.z * cv::Scale + 9.80665) < 0.00001);
  assert(cv::clipped(s));
  assert(!cv::clipped({100,-200,16384}));
  cv::Assembler a;
  for (size_t i = 0; i < 499; ++i) assert(!a.push({1,2,3}, 10000 + i * 2000));
  assert(a.push(s, 10000 + 499 * 2000));
  assert(a.batch.seq == 0 && a.batch.firstIndex == 0 && a.batch.t0Us == 10000);
  assert(a.batch.clippedCount == 1 && a.batch.samples[499].x == -32768);
  for (size_t i = 0; i < 500; ++i) {
    bool full = a.push({4,5,6}, 1010000 + i * 2000);
    assert(full == (i == 499));
  }
  assert(a.batch.seq == 1 && a.batch.firstIndex == 500);
  assert(a.batch.clippedCount == 0);
  a.push({9,9,9}, 2010000);
  a.discontinuity();
  for (size_t i = 0; i < 500; ++i) a.push({7,8,9}, 3000000 + i * 2000);
  assert(a.batch.seq == 2 && a.batch.segment == 1 && a.batch.firstIndex == 0);
  assert(a.batch.t0Us == 3000000 && a.batch.samples[0].x == 7);
  const char *ack = R"({"accepted":true,"batch_id":"cv.boot.1"})";
  assert(validAck(ack, std::strlen(ack), "cv.boot.1"));
  assert(!validAck(ack, std::strlen(ack), "cv.boot.2"));
  const char *wrongType = R"({"accepted":"true","batch_id":"cv.boot.1"})";
  assert(!validAck(wrongType, std::strlen(wrongType), "cv.boot.1"));
  const char *negative = R"({"accepted":false,"batch_id":"cv.boot.1"})";
  assert(!validAck(negative, std::strlen(negative), "cv.boot.1"));
  assert(!validAck("bad", 3, "cv.boot.1"));
  assert(!validAck("", 0, "cv.boot.1"));
  assert(!validAck(ack, 513, "cv.boot.1"));
  std::cout << "OK: signed decoding, scale, clipping, 500-frame batching, sequence and discontinuity\n";
}
