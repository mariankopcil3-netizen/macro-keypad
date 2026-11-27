// ============================================================================
// FINTR DECK FIRMWARE - src/main.cpp
// ESP32 DevKit v1 | ST7789 284x76 | 4x3 scan matrix | 2x EC11 encoders
// Non-blocking loop, ISR quadrature decoders, dirty-rect display updates.
// PC protocol: see pc_app/protocol.py (identical message names).
// ============================================================================

#include <Arduino.h>
#include <SPI.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ST7789.h>
#include <Preferences.h>
#include <string.h>
#include <stdlib.h>
#include <limits.h>
#include <math.h>

// ============================================================================
// PANEL
// ============================================================================
// Native orientation is 76x284; the UI is drawn in landscape (284x76).
#define PANEL_NATIVE_W 76
#define PANEL_NATIVE_H 284
#define PANEL_W 284
#define PANEL_H 76

// The 2.25" module has the backlight tied to VCC; BL is active LOW.
#define TFT_BL_ACTIVE_LOW 1
#if TFT_BL_ACTIVE_LOW
  #define TFT_BL_ON  LOW
  #define TFT_BL_OFF HIGH
#else
  #define TFT_BL_ON  HIGH
  #define TFT_BL_OFF LOW
#endif

// ============================================================================
// PIN DEFINITIONS
// ============================================================================
// ST7789 (hardware VSPI; SCK/MOSI pins are handed to SPI.begin() in setup())
#define TFT_CS   5
#define TFT_DC   16
#define TFT_RST  4
#define TFT_MOSI 23
#define TFT_SCK  18
#define TFT_BL   17

// Keypad matrix, electrically 4 lines x 3 lines (12 crosspoints).
// GPIO2 is a ROW (output) because the devkit's onboard LED holds it LOW when it
// is used as an input (the original "column 2 dead / lights column 1" bug).
// All three column pins (22/12/13) have working internal pull-ups.
byte ROW_PINS[4] = {15, 2, 19, 21};
byte COL_PINS[3] = {22, 12, 13};

// Encoder 1 - master volume
#define E1_CLK 32
#define E1_DT  33
#define E1_SW  25

// Encoder 2 - custom / application
#define E2_CLK 26
#define E2_DT  27
#define E2_SW  14

// ============================================================================
// TUNING
// ============================================================================
#define KEYPAD_SCAN_MS          10
#define VALUE_REFRESH_MS        120
#define KEY_DEBOUNCE_MS         25
#define KEYPAD_MAX_HELD         2   // diode-less matrix: 3+ keys ghost
#define ENC_SW_DEBOUNCE_MS      30
#define ENC_STEPS_PER_DETENT    4
#define FLASH_HOLD_MS           220
#define LIVE_TIMEOUT_MS         3000

// Wallpaper transfer block size, must match pc_app/protocol.py
#define WP_BLOCK_BYTES 768
#define FIRMWARE_VERSION 3

// Serial-driven hardware bring-up (B/C/R/I/P/E/K/Q/S commands).
#define BRINGUP 1

// ============================================================================
// GLOBAL OBJECTS
// ============================================================================
Adafruit_ST7789 tft = Adafruit_ST7789(TFT_CS, TFT_DC, TFT_RST);

// KEY_MAP: scan matrix (4 scan rows x 3 scan cols) -> key label.
// The keypad is wired physically 3 rows x 4 cols, MIRRORED horizontally
// (row 1: 4 3 2 1 / row 2: 8 7 6 5 / row 3: # 0 * 9), so this table is the
// transpose of that layout: scan row = physical column, scan col = physical
// row. If the board is rotated 180°, fix labels here (or run the PC
// calibration wizard, which stores the learned map in NVS).
const char KEY_MAP[4][3] = {
  {'4', '8', '#'},
  {'3', '7', '0'},
  {'2', '6', '*'},
  {'1', '5', '9'}
};

// Runtime map: starts as KEY_MAP, overridden by a valid NVS calibration.
char keys[4][3];

// Per-key software debounce (KEY_DEBOUNCE_MS), each crosspoint independently -
// pressing one key never blocks the others.
bool keyLastState[4][3] = {{false}};
bool keyStableState[4][3] = {{false}};
unsigned long keyLastDebounceMs[4][3] = {{0}};

// ============================================================================
// PROFILE MAP (4x3, matches the `keys` scan matrix; actions are looked up by
// key character, so the visual 3x4 layout of the PC app is unaffected)
// ============================================================================
#define PROFILE_COUNT 3

struct ProfileMap {
  const char *name;
  const char *action[4][3];
};

static const ProfileMap PROFILES[PROFILE_COUNT] = {
  {
    "EDIT",
    {
      {"app:spotify", "redo",     "save"},
      {"copy",        "cut",      "paste"},
      {"find",        "replace",  "select_all"},
      {"close",       "delete",   "enter"}
    }
  },
  {
    "MEDIA",
    {
      {"play",       "stop",       "next"},
      {"prev",       "vol_up",     "vol_down"},
      {"mute",       "rewind",     "forward"},
      {"fullscreen", "subtitles",  "snapshot"}
    }
  },
  {
    "CODE",
    {
      {"build",      "run",        "debug"},
      {"step_over",  "step_in",    "step_out"},
      {"breakpoint", "continue",   "stop"},
      {"terminal",   "format",     "commit"}
    }
  }
};

// ============================================================================
// ENCODERS (polled from loop(), Gray-code quadrature, no interrupts)
// ============================================================================
struct EncoderState {
  uint8_t clk;
  uint8_t dt;
  uint8_t lastState;
  int8_t accum;
  int32_t position;
  uint8_t swLastRaw;
  uint8_t swStable;
  uint32_t swChangeMs;
};

// Index = (previous << 2) | current, state = (CLK << 1) | DT.
static const int8_t DRAM_ATTR ENC_TABLE[16] = {
   0, -1,  1,  0,
   1,  0,  0, -1,
  -1,  0,  0,  1,
   0,  1, -1,  0
};

static EncoderState enc1 = {E1_CLK, E1_DT, 0, 0, 0, HIGH, HIGH, 0};
static EncoderState enc2 = {E2_CLK, E2_DT, 0, 0, 0, HIGH, HIGH, 0};

// One quadrature update per call; called from loop() on every iteration.
// EC11 detents turned by hand are ~1-2 ms apart, loop() iterates far faster,
// so no steps are lost without interrupts.
static void pollEncoder(EncoderState &enc) {
  uint8_t state = (uint8_t)((digitalRead(enc.clk) << 1) | digitalRead(enc.dt));
  if (state == enc.lastState) return;
  enc.accum += ENC_TABLE[(enc.lastState << 2) | state];
  enc.lastState = state;
  if (enc.accum >= ENC_STEPS_PER_DETENT) {
    enc.accum = 0;
    enc.position++;
  } else if (enc.accum <= -ENC_STEPS_PER_DETENT) {
    enc.accum = 0;
    enc.position--;
  }
}

int32_t reported1 = 0;
int32_t reported2 = 0;

// ============================================================================
// RUNTIME STATE
// ============================================================================
char lastPressedKey = 0;

int activeProfile = 0;
int telemetryCpu  = -1;
int telemetryGpu  = -1;
int telemetryRam  = -1;
int telemetryTemp = INT_MIN;

int enc1Percent = 0;int enc2Percent = 0;
bool enc1Muted  = false;
bool enc2Muted  = false;

bool pcLive = false;
uint32_t lastPcMs = 0;

char profileNames[PROFILE_COUNT][10];

unsigned long lastKeypadRead = 0;
unsigned long lastValueRefresh = 0;

char testText[32] = "";
bool testActive = false;
unsigned long testHoldUntil = 0;

// Keymap calibration: press the keys in the order printed on the keypad and the
// firmware learns which crosspoint belongs to which character.
static bool calibActive = false;
static uint8_t calibIndex = 0;
static unsigned long calibStartMs = 0;
static bool calibUsed[4][3] = {{false}};
static const char CALIB_ORDER[] = "123456789*0#";
static const unsigned long CALIB_TIMEOUT_MS = 240000;

// A keymap is valid only if it is a permutation of the 12 key characters; a
// corrupted/old NVS blob must never brick the keypad.
static bool keymapValid(const char *map) {
  if (map == nullptr || strlen(map) != 12) return false;
  for (uint8_t i = 0; i < 12; i++) {
    if (strchr(CALIB_ORDER, map[i]) == nullptr) return false;
    for (uint8_t j = i + 1; j < 12; j++)
      if (map[i] == map[j]) return false;
  }
  return true;
}

// Key flash badge
static bool flashActive = false;
static unsigned long flashStart = 0;
static char flashKeyChar = ' ';
static uint16_t accentColor = 0xAA1E;   // RGB565 of #A855F7

// Header status / key chip caches live in the common field cache.

// ============================================================================
// FRAMEBUFFER + DIRTY RECTS
// ============================================================================
// bgBuffer holds the wallpaper *plus* every static UI element (header, labels,
// ring tracks, bar tracks). Dynamic values are drawn into a small GFXcanvas16
// that is pre-filled from bgBuffer, then blitted to the panel - so the
// background is never repainted and nothing flickers.
static uint16_t bgBuffer[PANEL_W * PANEL_H];

#define CANVAS_W 100
#define CANVAS_H 40
static GFXcanvas16 *canvas = nullptr;

static int16_t fieldX = 0, fieldY = 0, fieldW = 0, fieldH = 0;

static bool wpReceiving = false;
static uint32_t wpTotal = 0;
static uint32_t wpRecv = 0;
static uint8_t wpTmp[WP_BLOCK_BYTES + 4];
static bool frameFromPc = false;   // bgBuffer holds the PC-rendered design frame

static Preferences prefs;

// ============================================================================
// PROTOTYPES
// ============================================================================
void initDisplay(void);
void buildDefaultBackground(void);
void renderChrome(void);
void drawFullFrame(void);
void updateValues(bool force);
void handleKeypad(void);
static void handleCalibrationScan(void);
static bool scanCrosspoint(uint8_t &outRow, uint8_t &outCol);
void handleWallpaper(const char *payload);
void pollSerial(void);
void handleCommand(const char *line);
void sendProfile(void);
bool keyToRowCol(char key, uint8_t &row, uint8_t &col);
void drawFlashChip(void);
uint16_t dim565(uint16_t color, uint8_t num, uint8_t den);

// ============================================================================
// SETUP
// ============================================================================
void setup() {
  Serial.setRxBufferSize(2048);   // wallpaper blocks are ~1 kB per line
  Serial.begin(115200);
  Serial.println(F("=== FINTR DECK FIRMWARE ==="));
  Serial.println(F("ESP32 | ST7789 284x76 | 4x3 scan matrix | 2x EC11"));
  Serial.printf("VER:%d\n", FIRMWARE_VERSION);

  pinMode(TFT_BL, OUTPUT);
  digitalWrite(TFT_BL, TFT_BL_ON);

  for (uint8_t i = 0; i < 4; i++) {
    pinMode(ROW_PINS[i], OUTPUT);
    digitalWrite(ROW_PINS[i], HIGH);
  }
  for (uint8_t i = 0; i < 3; i++) pinMode(COL_PINS[i], INPUT_PULLUP);

  pinMode(E1_CLK, INPUT_PULLUP);
  pinMode(E1_DT,  INPUT_PULLUP);
  pinMode(E1_SW,  INPUT_PULLUP);
  pinMode(E2_CLK, INPUT_PULLUP);
  pinMode(E2_DT,  INPUT_PULLUP);
  pinMode(E2_SW,  INPUT_PULLUP);

  enc1.lastState = (uint8_t)((digitalRead(E1_CLK) << 1) | digitalRead(E1_DT));
  enc2.lastState = (uint8_t)((digitalRead(E2_CLK) << 1) | digitalRead(E2_DT));

  for (int i = 0; i < PROFILE_COUNT; i++) {
    strncpy(profileNames[i], PROFILES[i].name, sizeof(profileNames[i]) - 1);
    profileNames[i][sizeof(profileNames[i]) - 1] = '\0';
  }

  memcpy(keys, KEY_MAP, sizeof(keys));   // default map before NVS override

  if (prefs.begin("keypad", true)) {
    String saved = prefs.getString("map", "");
    prefs.end();
    if (saved.length() == 12 && keymapValid(saved.c_str())) {
      for (uint8_t r = 0; r < 4; r++)
        for (uint8_t c = 0; c < 3; c++) keys[r][c] = saved[r * 3 + c];
      Serial.printf("KEYMAP:%s\n", saved.c_str());
    }
  }

  // Adafruit_ST7789(CS, DC, RST) uses the SPI library defaults unless the pins
  // are passed to SPI.begin(); TFT_SCK/TFT_MOSI take effect only here.
  SPI.begin(TFT_SCK, -1, TFT_MOSI, TFT_CS);

  canvas = new GFXcanvas16(CANVAS_W, CANVAS_H);
  if (canvas == nullptr) {
    Serial.println(F("BOOT: canvas alloc failed"));
    while (true) delay(1000);
  }
  initDisplay();

  Serial.println(F("--- READY ---"));
  Serial.printf("Pins: SCK=%d MOSI=%d CS=%d DC=%d RST=%d BL=%d\n",
                TFT_SCK, TFT_MOSI, TFT_CS, TFT_DC, TFT_RST, TFT_BL);
  Serial.printf("Matrix 4 rows %d/%d/%d/%d, 3 cols %d/%d/%d | panel %dx%d\n",
                ROW_PINS[0], ROW_PINS[1], ROW_PINS[2], ROW_PINS[3],
                COL_PINS[0], COL_PINS[1], COL_PINS[2],
                tft.width(), tft.height());
  sendProfile();
#if BRINGUP
  Serial.println(F("[BRINGUP] C:r|g|b|w|k=fill | R:0-3=rot | I=reinit |"
                   " X:<pin>=probe | E=raw enc | K=matrix | Q=probe all | S=sweep"));
#endif
}

// ============================================================================
// MAIN LOOP (non-blocking)
// ============================================================================
static bool rawPinDebug = false;
static uint32_t rawPinLast = 0;

static void rawPinScan(void) {
  // Every free GPIO except display (4/5/16/17/18/23), UART (1/3) and flash.
  static const uint8_t pins[13] = {2, 12, 13, 14, 15, 19, 21, 22,
                                   25, 26, 27, 32, 33};
  const uint8_t count = sizeof(pins);
  uint32_t signature = 0;

  for (uint8_t i = 0; i < count; i++) {
    for (uint8_t j = 0; j < count; j++) pinMode(pins[j], INPUT_PULLUP);
    pinMode(pins[i], OUTPUT);
    digitalWrite(pins[i], LOW);
    delayMicroseconds(200);
    uint16_t mask = 0;
    for (uint8_t j = 0; j < count; j++) {
      if (j != i && digitalRead(pins[j]) == LOW) mask |= (uint16_t)(1 << j);
    }
    pinMode(pins[i], INPUT_PULLUP);
    signature = (signature * 31u) ^ mask;
  }

  if (signature == rawPinLast) return;
  rawPinLast = signature;

  // Second pass only when the signature changed: print the pairs.
  for (uint8_t i = 0; i < count; i++) {
    for (uint8_t j = 0; j < count; j++) pinMode(pins[j], INPUT_PULLUP);
    pinMode(pins[i], OUTPUT);
    digitalWrite(pins[i], LOW);
    delayMicroseconds(200);
    Serial.printf("RAW %d ->", pins[i]);
    bool any = false;
    for (uint8_t j = 0; j < count; j++) {
      if (j == i) continue;
      if (digitalRead(pins[j]) == LOW) {
        Serial.printf(" %d", pins[j]);
        any = true;
      }
    }
    pinMode(pins[i], INPUT_PULLUP);
    if (any) Serial.println();
  }
  Serial.println(F("RAW end"));
}

// Quadrature for both encoders + debounced switches, called every loop().
static void pollEncoders(void) {
  pollEncoder(enc1);
  pollEncoder(enc2);
  for (uint8_t i = 0; i < 2; i++) {
    EncoderState &enc = (i == 0) ? enc1 : enc2;
    uint8_t sw = (uint8_t)digitalRead(i == 0 ? E1_SW : E2_SW);
    if (sw != enc.swLastRaw) {
      enc.swLastRaw = sw;
      enc.swChangeMs = millis();
    }
    if ((millis() - enc.swChangeMs) >= ENC_SW_DEBOUNCE_MS && sw != enc.swStable) {
      enc.swStable = sw;
      if (sw == LOW) {
        Serial.println(i == 0 ? F("ENC1_SW:1") : F("ENC2_SW:1"));
      }
    }
  }
}

void loop() {
  pollEncoders();

  // Encoder detents -> PC.
  int32_t pos1 = enc1.position;
  int32_t pos2 = enc2.position;
  if (pos1 != reported1) {
    reported1 = pos1;
    Serial.printf("ENC1:%ld\n", (long)pos1);
  }
  if (pos2 != reported2) {
    reported2 = pos2;
    Serial.printf("ENC2:%ld\n", (long)pos2);
  }

  pollSerial();

  if (rawPinDebug && (millis() & 0x7F) == 0) rawPinScan();

  unsigned long now = millis();

  if (calibActive && now - calibStartMs >= CALIB_TIMEOUT_MS) {
    calibActive = false;
    Serial.println(F("CAL:ABORT"));
    updateValues(true);
  }

  if (now - lastKeypadRead >= KEYPAD_SCAN_MS) {
    handleKeypad();
    lastKeypadRead = now;
  }

  if (!wpReceiving && flashActive && now - flashStart >= FLASH_HOLD_MS) {
    flashActive = false;
    drawFlashChip();
  }

  if (testActive) {
    if ((long)(now - testHoldUntil) >= 0) {
      testActive = false;
      drawFullFrame();
    }
  } else if (!wpReceiving && now - lastValueRefresh >= VALUE_REFRESH_MS) {
    updateValues(false);
    lastValueRefresh = now;
  }

  if (pcLive && now - lastPcMs > LIVE_TIMEOUT_MS) {
    pcLive = false;
    updateValues(true);
  }
}

// ============================================================================
// DISPLAY BASICS
// ============================================================================
void initDisplay(void) {
  tft.init(PANEL_NATIVE_W, PANEL_NATIVE_H);
  tft.invertDisplay(false);
  tft.setRotation(1);
  tft.setTextWrap(false);

  buildDefaultBackground();
  renderChrome();
  drawFullFrame();
}

static inline void bgFillRect(int16_t x, int16_t y, int16_t w, int16_t h,
                              uint16_t color) {
  for (int16_t row = 0; row < h; row++) {
    int16_t py = y + row;
    if (py < 0 || py >= PANEL_H) continue;
    for (int16_t col = 0; col < w; col++) {
      int16_t px = x + col;
      if (px < 0 || px >= PANEL_W) continue;
      bgBuffer[py * PANEL_W + px] = color;
    }
  }
}

static void bgToCanvas(int16_t x, int16_t y, int16_t w, int16_t h) {
  uint16_t *buf = canvas->getBuffer();
  for (int16_t row = 0; row < h; row++) {
    for (int16_t col = 0; col < w; col++) {
      int16_t px = x + col;
      int16_t py = y + row;
      uint16_t value = 0;
      if (px >= 0 && px < PANEL_W && py >= 0 && py < PANEL_H)
        value = bgBuffer[py * PANEL_W + px];
      buf[row * CANVAS_W + col] = value;
    }
  }
}

static void canvasToBg(int16_t x, int16_t y, int16_t w, int16_t h) {
  uint16_t *buf = canvas->getBuffer();
  for (int16_t row = 0; row < h; row++) {
    for (int16_t col = 0; col < w; col++) {
      int16_t px = x + col;
      int16_t py = y + row;
      if (px < 0 || px >= PANEL_W || py < 0 || py >= PANEL_H) continue;
      bgBuffer[py * PANEL_W + px] = buf[row * CANVAS_W + col];
    }
  }
}

static void blitCanvas(int16_t x, int16_t y, int16_t w, int16_t h) {
  uint16_t *buf = canvas->getBuffer();
  // NOTE: no startWrite()/endWrite() here - nesting it around
  // drawRGBBitmap() deadlocks the ESP32 SPI transaction.
  for (int16_t row = 0; row < h; row++)
    tft.drawRGBBitmap(x, y + row, buf + row * CANVAS_W, w, 1);
}

// Draws text into bgBuffer through the scratch canvas (absolute coordinates).
static void bgText(int16_t bx, int16_t by, int16_t bw, int16_t bh,
                   int16_t tx, int16_t ty, const char *text,
                   uint16_t color, uint8_t size) {
  bgToCanvas(bx, by, bw, bh);
  canvas->setTextSize(size);
  canvas->setTextColor(color);
  canvas->setCursor(tx - bx, ty - by);
  canvas->print(text);
  canvasToBg(bx, by, bw, bh);
}

static void canvasTextCentered(const char *text, int16_t cx, int16_t y,
                               uint16_t color, uint8_t size) {
  int16_t width = (int16_t)strlen(text) * 6 * size;
  canvas->setTextSize(size);
  canvas->setTextColor(color);
  canvas->setCursor(cx - width / 2, y);
  canvas->print(text);
}

static void canvasArcPts(int16_t cx, int16_t cy, int16_t r,
                         int16_t fromDeg, int16_t toDeg, uint16_t color) {
  int16_t prevX = 0, prevY = 0;
  bool have = false;
  for (int16_t deg = fromDeg; deg <= toDeg; deg += 3) {
    float rad = (float)deg * 0.0174532925f;
    int16_t x = cx + (int16_t)lroundf(cosf(rad) * r);
    int16_t y = cy + (int16_t)lroundf(sinf(rad) * r);
    if (have) canvas->drawLine(prevX, prevY, x, y, color);
    prevX = x;
    prevY = y;
    have = true;
  }
}

// ============================================================================
// BACKGROUND / STATIC CHROME
// ============================================================================
void buildDefaultBackground(void) {
  uint16_t deepest = tft.color565(0x0B, 0x0B, 0x10);
  uint16_t mid     = tft.color565(0x13, 0x0E, 0x1E);
  uint16_t upper   = tft.color565(0x1E, 0x13, 0x33);
  for (int16_t y = 0; y < PANEL_H; y++) {
    for (int16_t x = 0; x < PANEL_W; x++) {
      float t = (float)x / PANEL_W * 0.55f + (float)y / PANEL_H * 0.45f;
      uint16_t color;
      if (t < 0.55f) color = (t < 0.275f) ? deepest : mid;
      else color = upper;
      bgBuffer[y * PANEL_W + x] = color;
    }
  }
}

void renderChrome(void) {
  uint16_t accent = accentColor;
  uint16_t headerTop = tft.color565(0x14, 0x0E, 0x22);
  uint16_t headerLine = dim565(accent, 2, 5);
  uint16_t labelColor = tft.color565(0x9C, 0x8F, 0xB8);
  uint16_t textColor  = tft.color565(0xED, 0xE9, 0xFE);
  uint16_t trackColor = tft.color565(0x2A, 0x2A, 0x33);
  uint16_t accentSoft = tft.color565(0xC4, 0xB5, 0xFD);

  // Header bar + accent underline.
  bgFillRect(0, 0, PANEL_W, 15, headerTop);
  bgFillRect(0, 15, PANEL_W, 1, headerLine);
  bgText(11, 3, 78, 10, 11, 4, "FINTR DECK", textColor, 1);

  // Encoder ring tracks + labels.
  const int16_t centers[2] = {74, 210};
  const char *ringLabels[2] = {"E1", "E2"};
  for (uint8_t i = 0; i < 2; i++) {
    bgToCanvas(centers[i] - 16, 25, 32, 32);
    canvas->drawCircle(16, 16, 12, trackColor);
    canvas->drawCircle(16, 16, 13, trackColor);
    canvas->drawCircle(16, 16, 14, trackColor);
    canvasToBg(centers[i] - 16, 25, 32, 32);
    bgText(centers[i] - 16, 16, 32, 9, centers[i] - 6, 16,
           ringLabels[i], accentSoft, 1);
  }

  // Telemetry labels + bar tracks.
  const int16_t tCenters[3] = {108, 142, 176};
  const char *tLabels[3] = {"CPU", "GPU", "RAM"};
  for (uint8_t i = 0; i < 3; i++) {
    bgText(tCenters[i] - 12, 20, 24, 8, tCenters[i] - 9, 21,
           tLabels[i], labelColor, 1);
    bgFillRect(tCenters[i] - 11, 44, 22, 3, tft.color565(0x23, 0x23, 0x2E));
  }

  // MODE caption.
  bgText(PANEL_W / 2 - 20, 52, 40, 9, PANEL_W / 2 - 12, 52,
         "MODE", accentSoft, 1);
}

void drawFullFrame(void) {
  tft.drawRGBBitmap(0, 0, bgBuffer, PANEL_W, PANEL_H);
  updateValues(true);
}

// ============================================================================
// DYNAMIC FIELDS (dirty rects)
// ============================================================================
static char fieldCache[8][24];
static bool fieldValid[8];

static void resetFieldCache(void) {
  for (uint8_t i = 0; i < 8; i++) {
    fieldValid[i] = false;
    fieldCache[i][0] = '\0';
  }
}

static bool fieldBegin(uint8_t id, int16_t x, int16_t y, int16_t w, int16_t h,
                       const char *key) {
  if (id >= 8 || w > CANVAS_W || h > CANVAS_H) return false;
  if (fieldValid[id] && strncmp(fieldCache[id], key, sizeof(fieldCache[id]) - 1) == 0)
    return false;
  bgToCanvas(x, y, w, h);
  fieldX = x;
  fieldY = y;
  fieldW = w;
  fieldH = h;
  return true;
}

static void fieldCommit(uint8_t id, const char *key) {
  blitCanvas(fieldX, fieldY, fieldW, fieldH);
  strncpy(fieldCache[id], key, sizeof(fieldCache[id]) - 1);
  fieldCache[id][sizeof(fieldCache[id]) - 1] = '\0';
  fieldValid[id] = true;
}

static void drawRingField(uint8_t id, int16_t cx, int percent, bool muted,
                          const char *text) {
  char key[24];
  snprintf(key, sizeof(key), "%d|%d|%s", percent, muted ? 1 : 0, text);
  if (!fieldBegin(id, cx - 16, 16, 32, 40, key)) return;

  int16_t cy = 41 - 16;
  int16_t center = 16;
  if (!muted && percent > 0) {
    int16_t span = (int16_t)(percent * 3.6f);
    canvasArcPts(center, cy, 12, -90, -90 + span, dim565(accentColor, 2, 5));
    canvasArcPts(center, cy, 13, -90, -90 + span, accentColor);
    canvasArcPts(center, cy, 14, -90, -90 + span, accentColor);
  }
  canvasTextCentered(text, center, cy - 3, 0xFFFF, 1);
  fieldCommit(id, key);
}

static void drawTelemetryField(uint8_t id, int16_t cx, int value) {
  char text[8];
  if (value < 0) strcpy(text, "--%");
  else snprintf(text, sizeof(text), "%d%%", value);

  char key[16];
  snprintf(key, sizeof(key), "%d", value);
  if (!fieldBegin(id, cx - 13, 20, 26, 28, key)) return;

  bgToCanvas(cx - 13, 20, 26, 28);
  canvasTextCentered(text, 13, 9, 0xFFFF, 1);

  int fill = (value < 0) ? 0 : max(0, min(100, value));
  if (fill > 0) {
    uint16_t bar = accentColor;
    if (value > 90)      bar = tft.color565(0xEF, 0x44, 0x44);
    else if (value > 70) bar = tft.color565(0xF5, 0x9E, 0x0B);
    int16_t width = (int16_t)(22.0f * fill / 100.0f);
    if (width < 1) width = 1;
    canvas->fillRect(2, 24, width, 3, bar);
  }
  fieldCommit(id, key);
}

static void drawProfileField(void) {
  const char *name = profileNames[activeProfile];
  if (!fieldBegin(5, 92, 58, 100, 18, name)) return;
  bgToCanvas(92, 58, 100, 18);
  canvasTextCentered(name, 50, 3, accentColor, 2);
  fieldCommit(5, name);
}

static void drawStatusField(void) {
  const char *text = pcLive ? "LIVE" : "OFFLINE";
  uint16_t color = pcLive ? tft.color565(0x10, 0xB9, 0x81)
                          : tft.color565(0x6B, 0x72, 0x80);
  if (!fieldBegin(6, PANEL_W - 52, 2, 52, 11, text)) return;
  bgToCanvas(PANEL_W - 52, 2, 52, 11);
  canvas->fillCircle(4, 5, 2, color);
  canvas->setTextSize(1);
  canvas->setTextColor(color);
  canvas->setCursor(10, 1);
  canvas->print(text);
  fieldCommit(6, text);
}

void drawFlashChip(void) {
  if (!flashActive) {
    if (!fieldBegin(7, PANEL_W / 2 - 30, 1, 60, 12, "")) return;
    bgToCanvas(PANEL_W / 2 - 30, 1, 60, 12);
    fieldCommit(7, "");
    return;
  }
  char text[10];
  snprintf(text, sizeof(text), "KEY %c", flashKeyChar);
  if (!fieldBegin(7, PANEL_W / 2 - 30, 1, 60, 12, text)) return;
  bgToCanvas(PANEL_W / 2 - 30, 1, 60, 12);
  canvas->fillRect(2, 0, 56, 12, accentColor);
  canvas->setTextSize(1);
  canvas->setTextColor(0x0000);
  int16_t width = (int16_t)strlen(text) * 6;
  canvas->setCursor(30 - width / 2, 2);
  canvas->print(text);
  fieldCommit(7, text);
}

void updateValues(bool force) {
  if (force) resetFieldCache();

  char text[8];
  if (enc1Muted) strcpy(text, "MUTE");
  else snprintf(text, sizeof(text), "%d%%", enc1Percent);
  drawRingField(0, 74, enc1Percent, enc1Muted, text);

  if (enc2Muted) strcpy(text, "MUTE");
  else snprintf(text, sizeof(text), "%d%%", enc2Percent);
  drawRingField(1, 210, enc2Percent, enc2Muted, text);

  drawTelemetryField(2, 108, telemetryCpu);
  drawTelemetryField(3, 142, telemetryGpu);
  drawTelemetryField(4, 176, telemetryRam);
  drawProfileField();
  drawStatusField();
  drawFlashChip();
}

uint16_t dim565(uint16_t color, uint8_t num, uint8_t den) {
  uint8_t r = ((color >> 11) & 0x1F) * num / den;
  uint8_t g = ((color >> 5) & 0x3F) * num / den;
  uint8_t b = (color & 0x1F) * num / den;
  return (r << 11) | (g << 5) | b;
}

// ============================================================================
// KEYPAD
// ============================================================================
bool keyToRowCol(char key, uint8_t &row, uint8_t &col) {
  for (uint8_t r = 0; r < 4; r++) {
    for (uint8_t c = 0; c < 3; c++) {
      if (keys[r][c] == key) {
        row = r;
        col = c;
        return true;
      }
    }
  }
  return false;
}

void sendProfile(void) {
  Serial.print(F("PROFILE:"));
  Serial.print(activeProfile + 1);
  Serial.print(':');
  Serial.println(profileNames[activeProfile]);
}

static char lastKeyChar = 0;
static unsigned long lastKeyMs = 0;

static void emitKey(char key) {
  if (key == 0) return;

  unsigned long now = millis();
  // Bounce/ghost guard: the same character cannot fire twice inside 60 ms.
  if (key == lastKeyChar && (now - lastKeyMs) < 60) return;
  lastKeyChar = key;
  lastKeyMs = now;

  lastPressedKey = key;

  Serial.printf("KEY:%c\n", key);

  flashActive = true;
  flashStart = now;
  flashKeyChar = key;
  if (!wpReceiving) updateValues(false);

  uint8_t row = 0;
  uint8_t col = 0;
  if (keyToRowCol(key, row, col)) {
    Serial.print(F("MACRO:"));
    Serial.println(PROFILES[activeProfile].action[row][col]);
  }
}

void handleKeypad(void) {
  if (calibActive) {          // direct crosspoint scan, independent of keymap
    handleCalibrationScan();
    return;
  }
  unsigned long now = millis();
  if (now < 500) return;      // settle: pins float right after reset

  for (uint8_t r = 0; r < 4; r++) {
    // All rows idle HIGH; only the scanned row goes LOW. 20 us settle absorbs
    // breadboard wire capacitance, then all three columns are read and the
    // row is released again - no cross-talk between scan rows.
    digitalWrite(ROW_PINS[r], LOW);
    delayMicroseconds(20);
    for (uint8_t c = 0; c < 3; c++) {
      bool pressed = digitalRead(COL_PINS[c]) == LOW;
      if (pressed != keyLastState[r][c]) {
        keyLastState[r][c] = pressed;
        keyLastDebounceMs[r][c] = now;
      } else if (pressed != keyStableState[r][c] &&
                 (now - keyLastDebounceMs[r][c]) >= KEY_DEBOUNCE_MS) {
        keyStableState[r][c] = pressed;
        if (!pressed) continue;
        uint8_t held = 0;
        for (uint8_t rr = 0; rr < 4; rr++)
          for (uint8_t cc = 0; cc < 3; cc++)
            if (keyStableState[rr][cc]) held++;
        if (held > KEYPAD_MAX_HELD) {
          // Ghosting from 3+ simultaneous keys is physical on a diode-less
          // matrix - report it, but never act on it.
          Serial.printf("KEY:IGNORED:%c\n", keys[r][c]);
        } else {
          emitKey(keys[r][c]);
        }
      }
    }
    digitalWrite(ROW_PINS[r], HIGH);
  }
}

// ============================================================================
// CALIBRATION (direct crosspoint scan, independent of the keymap)
// ============================================================================
static uint8_t calibCandRow = 255, calibCandCol = 255;
static unsigned long calibCandMs = 0;

// Drives one row at a time and reads the columns; reports the crosspoint only
// when EXACTLY one column is low, so simultaneous presses are ignored instead
// of being misassigned.
static bool scanCrosspoint(uint8_t &outRow, uint8_t &outCol) {
  for (uint8_t r = 0; r < 4; r++) {
    for (uint8_t c = 0; c < 3; c++) pinMode(COL_PINS[c], INPUT_PULLUP);
    pinMode(ROW_PINS[r], OUTPUT);
    digitalWrite(ROW_PINS[r], LOW);
    delayMicroseconds(200);
    uint8_t found = 255;
    bool multi = false;
    for (uint8_t c = 0; c < 3; c++) {
      if (digitalRead(COL_PINS[c]) == LOW) {
        if (found != 255) multi = true;
        else found = c;
      }
    }
    digitalWrite(ROW_PINS[r], HIGH);
    pinMode(ROW_PINS[r], INPUT_PULLUP);
    if (found != 255 && !multi) {
      outRow = r;
      outCol = found;
      return true;
    }
  }
  return false;
}

static void handleCalibrationScan(void) {
  static uint8_t lastDupRow = 255, lastDupCol = 255;
  uint8_t r = 255, c = 255;
  if (!scanCrosspoint(r, c)) {
    calibCandRow = 255;
    lastDupRow = 255;
    return;
  }
  if (r != calibCandRow || c != calibCandCol) {
    calibCandRow = r;
    calibCandCol = c;
    calibCandMs = millis();
    return;                     // wait until the press is stable (~60 ms)
  }
  if (millis() - calibCandMs < 60) return;
  calibCandRow = 255;
  calibCandCol = 255;

  if (calibUsed[r][c]) {
    if (r != lastDupRow || c != lastDupCol) {
      lastDupRow = r;
      lastDupCol = c;
      Serial.printf("CAL:DUPLICATE:%d:%d:already used\n", r, c);
    }
    return;
  }
  lastDupRow = 255;
  char target = CALIB_ORDER[calibIndex];
  calibUsed[r][c] = true;
  keys[r][c] = target;
  calibIndex++;
  Serial.printf("CAL:%d:(%d,%d):%c\n", calibIndex, r, c, target);
  if (calibIndex >= 12) {
    calibActive = false;
    char map[13];
    for (uint8_t rr = 0; rr < 4; rr++)
      for (uint8_t cc = 0; cc < 3; cc++) map[rr * 3 + cc] = keys[rr][cc];
    map[12] = '\0';
    prefs.begin("keypad", false);
    prefs.putString("map", map);
    prefs.end();
    Serial.printf("KEYMAP:%s\n", map);
    Serial.println(F("CAL:DONE"));
    drawFullFrame();
  } else {
    Serial.printf("CAL:next:%c\n", CALIB_ORDER[calibIndex]);
  }
}

// ============================================================================
// SERIAL
// ============================================================================
static char rxLine[1200];
static uint16_t rxLen = 0;

void pollSerial(void) {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();

    if (c == '\n' || c == '\r') {
      if (rxLen > 0) {
        rxLine[rxLen] = '\0';
        handleCommand(rxLine);
        rxLen = 0;
      }
    } else if (rxLen < sizeof(rxLine) - 1) {
      rxLine[rxLen++] = c;
    } else {
      rxLen = 0;   // overflow -> discard the whole line
    }
  }
}

#if BRINGUP
static void probePin(int pin) {
  if (pin < 0 || pin > 39) {
    Serial.println(F("X:<0-39>"));
    return;
  }
  pinMode(pin, INPUT_PULLUP);
  delay(3);
  int up = digitalRead(pin);
  pinMode(pin, INPUT_PULLDOWN);
  delay(3);
  int down = digitalRead(pin);
  pinMode(pin, INPUT_PULLUP);

  Serial.printf("PROBE %d: pullup=%d pulldown=%d", pin, up, down);
  if (up == 1 && down == 0)       Serial.println(F(" -> floating (nothing connected)"));
  else if (up == 1 && down == 1)  Serial.println(F(" -> tied to 3V3"));
  else if (up == 0 && down == 0)  Serial.println(F(" -> tied to GND"));
  else                            Serial.println(F(" -> unstable"));
}

static void scanMatrix(void) {
  for (uint8_t r = 0; r < 4; r++) {
    pinMode(ROW_PINS[r], OUTPUT);
    digitalWrite(ROW_PINS[r], HIGH);
  }
  for (uint8_t c = 0; c < 3; c++) pinMode(COL_PINS[c], INPUT_PULLUP);
  delay(2);

  Serial.printf("MATRIX 4 rows %d/%d/%d/%d, 3 cols %d/%d/%d:",
                ROW_PINS[0], ROW_PINS[1], ROW_PINS[2], ROW_PINS[3],
                COL_PINS[0], COL_PINS[1], COL_PINS[2]);
  for (uint8_t r = 0; r < 4; r++) {
    digitalWrite(ROW_PINS[r], LOW);
    delayMicroseconds(200);
    Serial.print(' ');
    for (uint8_t c = 0; c < 3; c++) Serial.print(digitalRead(COL_PINS[c]));
    digitalWrite(ROW_PINS[r], HIGH);
  }
  Serial.println(F("   (0=pressed/short, 1=idle)"));
}
#endif

// ============================================================================
// WALLPAPER (W:B:<bytes> / W:<block>:<base64> / W:E, ACK per block)
// ============================================================================
static int b64Decode(const char *in, uint8_t *out, int maxOut) {
  static const char *tbl =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  int len = (int)strlen(in);
  while (len > 0 && (in[len - 1] == '=' || in[len - 1] == '\r' ||
                     in[len - 1] == '\n')) {
    len--;
  }
  int outLen = 0;
  int val = 0, valb = -8;
  for (int i = 0; i < len; i++) {
    const char *p = strchr(tbl, in[i]);
    if (p == nullptr || in[i] == '\0') continue;
    val = (val << 6) + (int)(p - tbl);
    valb += 6;
    if (valb >= 0) {
      if (outLen < maxOut) out[outLen++] = (uint8_t)((val >> valb) & 0xFF);
      valb -= 8;
    }
  }
  return outLen;
}

void handleWallpaper(const char *payload) {
  if (payload[0] == 'B' && payload[1] == ':') {
    wpTotal = (uint32_t)strtoul(payload + 2, nullptr, 10);
    wpRecv = 0;
    wpReceiving = true;
    if (wpTotal != (uint32_t)(PANEL_W * PANEL_H * 2)) {
      wpTotal = 0;
      wpReceiving = false;
      Serial.println(F("WP:BAD"));
      return;
    }
    Serial.printf("WP:BEGIN:%lu\n", (unsigned long)wpTotal);
    return;
  }

  if (payload[0] == 'E' && payload[1] == '\0') {
    if (wpTotal == 0 || wpRecv < wpTotal) {
      Serial.printf("WP:TRUNC:%lu:%lu\n", (unsigned long)wpRecv,
                    (unsigned long)wpTotal);
      wpReceiving = false;
      return;
    }
    wpReceiving = false;
    frameFromPc = true;      // frame already contains the static design
    drawFullFrame();
    Serial.printf("WP:OK:%lu\n", (unsigned long)wpRecv);
    return;
  }

  const char *sep = strchr(payload, ':');
  if (sep == nullptr || wpTotal == 0) return;

  uint32_t block = (uint32_t)strtoul(payload, nullptr, 10);
  uint32_t byteOffset = block * WP_BLOCK_BYTES;
  if (byteOffset >= wpTotal) return;

  int n = b64Decode(sep + 1, wpTmp, (int)sizeof(wpTmp));
  if (n <= 0) return;

  uint32_t pixel = byteOffset / 2;
  uint32_t maxPixel = wpTotal / 2;
  for (int i = 0; i + 1 < n && pixel < maxPixel; i += 2) {
    bgBuffer[pixel++] = (uint16_t)((wpTmp[i] << 8) | wpTmp[i + 1]);
  }
  uint32_t received = byteOffset + (uint32_t)n;
  if (received > wpRecv) wpRecv = received;
  Serial.printf("WP:ACK:%lu\n", (unsigned long)block);
}

// Clear per-key debounce state (clears stuck pressed entries that can survive
// a reflash / noisy boot and would block real presses).
static void resetKeypadState(void) {
  for (uint8_t r = 0; r < 4; r++)
    for (uint8_t c = 0; c < 3; c++) {
      keyLastState[r][c] = false;
      keyStableState[r][c] = false;
      keyLastDebounceMs[r][c] = 0;
    }
}

void handleCommand(const char *line) {
  pcLive = true;
  lastPcMs = millis();

  if (line[0] == '?' && line[1] == '\0') {
    // App handshake: always cancels stuck calibration / raw debug modes so the
    // keypad can never stay deaf.
    resetKeypadState();
    if (calibActive) {
      calibActive = false;
      Serial.println(F("CAL:ABORT"));
    }
    if (rawPinDebug) {
      rawPinDebug = false;
      for (uint8_t i = 0; i < 4; i++) {
        pinMode(ROW_PINS[i], OUTPUT);
        digitalWrite(ROW_PINS[i], HIGH);
      }
      for (uint8_t i = 0; i < 3; i++) pinMode(COL_PINS[i], INPUT_PULLUP);
      Serial.println(F("RAWPINS:0"));
    }
    Serial.printf("VER:%d\n", FIRMWARE_VERSION);
    return;
  }
  if (line[0] == 'c' && line[1] == '\0') {
    resetKeypadState();
    calibActive = true;
    calibIndex = 0;
    calibStartMs = millis();
    memset(calibUsed, 0, sizeof(calibUsed));
    Serial.println(F("CAL:START"));
    Serial.printf("CAL:next:%c\n", CALIB_ORDER[0]);
    return;
  }
  if (line[0] == 'G' && line[1] == ':') {
    // G:<reported><target> -> the crosspoint currently reporting `reported`
    // becomes `target` (used by the PC calibration wizard).
    const char *chars = line + 2;
    if (strlen(chars) < 2 || strchr(CALIB_ORDER, chars[0]) == nullptr ||
        strchr(CALIB_ORDER, chars[1]) == nullptr) {
      Serial.println(F("G:BAD"));
      return;
    }
    char reported = chars[0];
    char target = chars[1];
    uint8_t row = 0, col = 0;
    if (!keyToRowCol(reported, row, col)) {
      Serial.printf("G:NOTFOUND:%c\n", reported);
      return;
    }
    keys[row][col] = target;
    char map[13];
    for (uint8_t r = 0; r < 4; r++)
      for (uint8_t c = 0; c < 3; c++) map[r * 3 + c] = keys[r][c];
    map[12] = '\0';
    prefs.begin("keypad", false);
    prefs.putString("map", map);
    prefs.end();
    Serial.printf("KEYMAP:%s\n", map);
    return;
  }
  if (line[0] == 'M' && line[1] == ':') {
    // M:123456789*0#  -> keymap in scan order (row major, 4 rows x 3 cols)
    const char *map = line + 2;
    if (!keymapValid(map)) {
      Serial.println(F("M:BADMAP"));
      return;
    }
    resetKeypadState();
    for (uint8_t r = 0; r < 4; r++)
      for (uint8_t c = 0; c < 3; c++) keys[r][c] = map[r * 3 + c];
    prefs.begin("keypad", false);
    prefs.putString("map", map);
    prefs.end();
    Serial.printf("KEYMAP:%s\n", map);
    return;
  }

#if BRINGUP
  if (line[0] == 'C' && line[1] == ':') {
    char k = line[2];
    uint16_t col = ST77XX_BLACK;
    bool ok = true;
    switch (k) {
      case 'r': col = ST77XX_RED;     break;
      case 'g': col = ST77XX_GREEN;   break;
      case 'b': col = ST77XX_BLUE;    break;
      case 'w': col = ST77XX_WHITE;   break;
      case 'k': col = ST77XX_BLACK;   break;
      case 'y': col = ST77XX_YELLOW;  break;
      case 'c': col = ST77XX_CYAN;    break;
      case 'm': col = ST77XX_MAGENTA; break;
      default: ok = false; break;
    }
    if (!ok) Serial.println(F("C:r|g|b|w|k|y|c|m"));
    else {
      tft.fillScreen(col);
      Serial.printf("FILL:%c\n", k);
    }
    return;
  }
  if (line[0] == 'R' && line[1] == ':') {
    int n = atoi(line + 2);
    if (n < 0 || n > 3) { Serial.println(F("R:0-3")); return; }
    tft.setRotation(n);
    Serial.printf("ROT:%d %dx%d\n", n, tft.width(), tft.height());
    return;
  }
  if (line[0] == 'I' && line[1] == '\0') {
    tft.init(PANEL_NATIVE_W, PANEL_NATIVE_H);
    tft.invertDisplay(false);
    tft.setRotation(1);
    tft.setTextWrap(false);
    if (!frameFromPc) {
      buildDefaultBackground();
      renderChrome();
    }
    drawFullFrame();
    Serial.printf("REINIT %dx%d\n", tft.width(), tft.height());
    return;
  }
  if (line[0] == 'X' && line[1] == ':') {
    probePin(atoi(line + 2));
    return;
  }
  if (line[0] == 'J' && line[1] == '\0') {
    static bool invertOn = false;
    invertOn = !invertOn;
    tft.invertDisplay(invertOn);
    Serial.print(F("INVERT:"));
    Serial.println(invertOn ? 1 : 0);
    return;
  }
  if (line[0] == 'E' && line[1] == '\0') {
    static bool rawEcho = false;
    rawEcho = !rawEcho;
    Serial.print(F("RAWMODE:"));
    Serial.println(rawEcho ? 1 : 0);
    return;
  }
  if (line[0] == 'D' && line[1] == '\0') {
    rawPinDebug = !rawPinDebug;
    rawPinLast = 0xFFFFFFFF;
    if (!rawPinDebug) {
      for (uint8_t i = 0; i < 4; i++) {
        pinMode(ROW_PINS[i], OUTPUT);
        digitalWrite(ROW_PINS[i], HIGH);
      }
      for (uint8_t i = 0; i < 3; i++) pinMode(COL_PINS[i], INPUT_PULLUP);
    }
    Serial.printf("RAWPINS:%d\n", rawPinDebug ? 1 : 0);
    return;
  }
  if (line[0] == 'K' && line[1] == '\0') {
    scanMatrix();
    return;
  }
  if (line[0] == 'Q' && line[1] == '\0') {
    static const uint8_t pins[] = {5, 16, 4, 18, 23, 17, 32, 33, 25, 26, 27,
                                   14, 15, 2, 19, 21, 22, 12, 13};
    for (uint8_t i = 0; i < sizeof(pins); i++) probePin(pins[i]);
    return;
  }
  if (line[0] == 'S' && line[1] == '\0') {
    static const uint8_t pins[] = {4, 5, 13, 14, 15, 16, 17, 18, 19, 21, 22,
                                   23, 25, 26, 27, 32, 33};
    Serial.println(F("SWEEP START (watch backlight)"));
    for (uint8_t i = 0; i < sizeof(pins); i++) {
      uint8_t p = pins[i];
      pinMode(p, OUTPUT);
      Serial.printf("SWEEP %d HIGH\n", p);
      digitalWrite(p, HIGH);
      delay(400);
      Serial.printf("SWEEP %d LOW\n", p);
      digitalWrite(p, LOW);
      delay(400);
      pinMode(p, INPUT_PULLUP);
    }
    initDisplay();
    Serial.println(F("SWEEP DONE"));
    return;
  }
#endif

  if (line[0] == 'A' && line[1] == ':') {
    if (strlen(line) >= 8) {
      unsigned long v = strtoul(line + 2, nullptr, 16);
      accentColor = tft.color565((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF);
      if (!wpReceiving) {
        if (frameFromPc) {
          updateValues(true);
        } else {
          renderChrome();
          drawFullFrame();
        }
      }
      Serial.print(F("ACCENT:"));
      Serial.println(line + 2);
    }
    return;
  }
  if (line[0] == 'F' && line[1] == ':') {
    if (line[2]) flashKeyChar = line[2];
    flashActive = true;
    flashStart = millis();
    updateValues(false);
    return;
  }
  if (line[0] == 'T' && line[1] == ':') {
    char buf[sizeof(rxLine)];
    strncpy(buf, line + 2, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = '\0';

    char *save = nullptr;
    for (char *tok = strtok_r(buf, ";", &save); tok != nullptr;
         tok = strtok_r(nullptr, ";", &save)) {
      char *eq = strchr(tok, '=');
      if (eq == nullptr) continue;
      *eq = '\0';
      int value = atoi(eq + 1);

      if (strcmp(tok, "CPU") == 0)       telemetryCpu = value;
      else if (strcmp(tok, "GPU") == 0)  telemetryGpu = value;
      else if (strcmp(tok, "RAM") == 0)  telemetryRam = value;
      else if (strcmp(tok, "TEMP") == 0) telemetryTemp = value;
    }
    lastValueRefresh = 0;   // redraw on the next loop pass
    return;
  }
  if (line[0] == 'V' && line[1] == ':') {
    // V:E1=42;E2=17;M1=0;M2=0  (percent + mute state from the PC mixer)
    char buf[sizeof(rxLine)];
    strncpy(buf, line + 2, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = '\0';

    char *save = nullptr;
    for (char *tok = strtok_r(buf, ";", &save); tok != nullptr;
         tok = strtok_r(nullptr, ";", &save)) {
      char *eq = strchr(tok, '=');
      if (eq == nullptr) continue;
      *eq = '\0';
      int value = atoi(eq + 1);
      if (strcmp(tok, "E1") == 0)      enc1Percent = value;
      else if (strcmp(tok, "E2") == 0) enc2Percent = value;
      else if (strcmp(tok, "M1") == 0) enc1Muted = value != 0;
      else if (strcmp(tok, "M2") == 0) enc2Muted = value != 0;
    }
    lastValueRefresh = 0;
    return;
  }
  if (line[0] == 'P' && line[1] == ':') {
    int n = atoi(line + 2);
    if (n >= 1) {
      activeProfile = (n - 1) % PROFILE_COUNT;
      sendProfile();
      lastValueRefresh = 0;
    }
    return;
  }
  if (line[0] == 'N' && line[1] == ':') {
    const char *sep = strchr(line + 2, ':');
    int n = atoi(line + 2);
    if (sep != nullptr && n >= 1 && n <= PROFILE_COUNT) {
      strncpy(profileNames[n - 1], sep + 1, sizeof(profileNames[0]) - 1);
      profileNames[n - 1][sizeof(profileNames[0]) - 1] = '\0';
      Serial.print(F("PAGENAME:"));
      Serial.print(n);
      Serial.print(':');
      Serial.println(profileNames[n - 1]);
      sendProfile();
      lastValueRefresh = 0;
    }
    return;
  }
  if (line[0] == 'H' && line[1] == ':') {
    strncpy(testText, line + 2, sizeof(testText) - 1);
    testText[sizeof(testText) - 1] = '\0';
    tft.fillScreen(ST77XX_BLACK);
    tft.drawRect(0, 0, tft.width(), tft.height(), ST77XX_GREEN);
    int len = strlen(testText);
    if (len > 0) {
      uint8_t size = 2;
      while (size > 1 && (6 * size * len) > tft.width() - 8) size--;
      tft.setTextSize(size);
      tft.setTextColor(ST77XX_YELLOW);
      int16_t x = (tft.width() - 6 * size * len) / 2;
      int16_t y = (tft.height() - 8 * size) / 2;
      if (x < 2) x = 2;
      if (y < 2) y = 2;
      tft.setCursor(x, y);
      tft.print(testText);
    }
    testActive = true;
    testHoldUntil = millis() + 5000;
    Serial.print(F("DISPLAY:"));
    Serial.println(testText);
    return;
  }
  if (line[0] == 'W' && line[1] == ':') {
    handleWallpaper(line + 2);
    return;
  }
}
