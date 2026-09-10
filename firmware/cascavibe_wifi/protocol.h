#pragma once
#include <stdint.h>
#include <stddef.h>

namespace cv {
constexpr size_t SamplesPerBatch = 500;
constexpr uint32_t RateHz = 500;
constexpr uint32_t PeriodUs = 2000;
constexpr float Scale = 9.80665f / 16384.0f;
struct Sample { int16_t x, y, z; };
static_assert(sizeof(Sample) == 6, "Amostra XYZ deve ter 6 bytes");
inline int16_t decode16(const uint8_t *p) {
  const uint16_t v = (uint16_t(p[0]) << 8) | p[1];
  return v < 32768 ? int16_t(v) : int16_t(int32_t(v) - 65536);
}
inline Sample decode(const uint8_t *p) {
  return {decode16(p), decode16(p + 2), decode16(p + 4)};
}
inline bool clipped(Sample s) {
  return s.x >= 32700 || s.x <= -32700 || s.y >= 32700 ||
         s.y <= -32700 || s.z >= 32700 || s.z <= -32700;
}
struct Batch {
  uint32_t seq, segment;
  uint64_t firstIndex, t0Us;
  uint32_t clippedCount;
  Sample samples[SamplesPerBatch];
};
// Uma mudança de segmento invalida o lote parcial. Nunca emenda uma lacuna.
struct Assembler {
  Batch batch{};
  size_t used = 0;
  uint32_t nextSeq = 0, segment = 0;
  uint64_t nextIndex = 0;
  void discontinuity() { used = 0; nextIndex = 0; ++segment; }
  bool push(Sample s, uint64_t sampleUs) {
    if (!used) {
      batch.seq = nextSeq;
      batch.segment = segment;
      batch.firstIndex = nextIndex;
      batch.t0Us = sampleUs;
      batch.clippedCount = 0;
    }
    batch.samples[used++] = s;
    ++nextIndex;
    batch.clippedCount += clipped(s);
    if (used != SamplesPerBatch) return false;
    used = 0;
    ++nextSeq;
    return true;
  }
};
}
