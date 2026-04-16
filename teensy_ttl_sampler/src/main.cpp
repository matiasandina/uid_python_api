#include <Arduino.h>

#ifndef FW_VERSION
#define FW_VERSION "0.1.0"
#endif

#ifndef FW_GIT_HASH
#define FW_GIT_HASH "unknown"
#endif

// Pin assignment (TTL inputs):
//   CH0 -> pin 2
//   CH1 -> pin 3
//   CH2 -> pin 4
//   CH3 -> pin 5
// Update these pins only if your wiring changes.
static constexpr uint8_t kPins[4] = {2, 3, 4, 5};

static constexpr uint32_t kSamplingRateHz = 20000;
static constexpr uint32_t kSamplePeriodUs = 1000000UL / kSamplingRateHz; // 50 us
static constexpr uint16_t kFrameSize = 2048;
static constexpr uint8_t kMagic0 = 0xAA;
static constexpr uint8_t kMagic1 = 0x55;

static uint8_t g_payload[kFrameSize];
static uint16_t g_sampleCount = 0;
static uint32_t g_frameId = 0;
static uint64_t g_tUsFirstSample = 0;

static uint32_t g_nextSampleUs = 0;
static bool g_handshakeSent = false;

static inline uint8_t sampleMask() {
  // Use fast GPIO reads for deterministic 20 kHz sampling.
  uint8_t mask = 0;
  mask |= (digitalReadFast(kPins[0]) ? 1 : 0) << 0;
  mask |= (digitalReadFast(kPins[1]) ? 1 : 0) << 1;
  mask |= (digitalReadFast(kPins[2]) ? 1 : 0) << 2;
  mask |= (digitalReadFast(kPins[3]) ? 1 : 0) << 3;
  return mask;
}

static bool serialActive() {
  return Serial && Serial.dtr();
}

static void sendHandshake() {
  Serial.print("#TTL_HANDSHAKE {");
  Serial.print("\"version\":1,");
  Serial.print("\"sampling_rate_hz\":");
  Serial.print(kSamplingRateHz);
  Serial.print(",\"frame_size\":");
  Serial.print(kFrameSize);
  Serial.print(",\"channel_map\":[1,2,3,4],");
  Serial.print("\"firmware_version\":\"");
  Serial.print(FW_VERSION);
  Serial.print("\",");
  Serial.print("\"git_hash\":\"");
  Serial.print(FW_GIT_HASH);
  Serial.println("\"}");
}

static void writeLe16(uint8_t* dst, uint16_t v) {
  dst[0] = static_cast<uint8_t>(v & 0xFF);
  dst[1] = static_cast<uint8_t>((v >> 8) & 0xFF);
}

static void writeLe32(uint8_t* dst, uint32_t v) {
  dst[0] = static_cast<uint8_t>(v & 0xFF);
  dst[1] = static_cast<uint8_t>((v >> 8) & 0xFF);
  dst[2] = static_cast<uint8_t>((v >> 16) & 0xFF);
  dst[3] = static_cast<uint8_t>((v >> 24) & 0xFF);
}

static void writeLe64(uint8_t* dst, uint64_t v) {
  for (uint8_t i = 0; i < 8; ++i) {
    dst[i] = static_cast<uint8_t>((v >> (8 * i)) & 0xFF);
  }
}

static void flushFrameIfReady() {
  if (g_sampleCount < kFrameSize) {
    return;
  }

  if (serialActive()) {
    const uint16_t nSamples = g_sampleCount;
    const uint16_t totalSize = static_cast<uint16_t>(16 + nSamples);

    if (Serial.availableForWrite() >= totalSize) {
      uint8_t header[16];
      header[0] = kMagic0;
      header[1] = kMagic1;
      writeLe32(&header[2], g_frameId);
      writeLe16(&header[6], nSamples);
      writeLe64(&header[8], g_tUsFirstSample);

      Serial.write(header, sizeof(header));
      Serial.write(g_payload, nSamples);
    }
  }

  g_frameId++;
  g_sampleCount = 0;
}

void setup() {
  for (uint8_t i = 0; i < 4; ++i) {
    pinMode(kPins[i], INPUT);
  }

  Serial.begin(115200);
  g_nextSampleUs = micros();
}

void loop() {
  const bool active = serialActive();
  if (active && !g_handshakeSent) {
    sendHandshake();
    g_handshakeSent = true;
  } else if (!active) {
    g_handshakeSent = false;
  }

  const uint32_t nowUs = micros();
  while (static_cast<int32_t>(nowUs - g_nextSampleUs) >= 0) {
    if (g_sampleCount == 0) {
      g_tUsFirstSample = static_cast<uint64_t>(g_nextSampleUs);
    }

    g_payload[g_sampleCount++] = sampleMask();
    g_nextSampleUs += kSamplePeriodUs;

    if (g_sampleCount >= kFrameSize) {
      flushFrameIfReady();
    }
  }
}
