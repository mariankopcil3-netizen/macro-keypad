// ============================================================================
// MACRO KEYPAD FIRMWARE - src/main.cpp
// ESP32 DevKit v1 | ST7789 display | 4x3 Keypad | 2x EC11 Encoders
// Non-blocking loop, quadrature decoder, debounce, Serial telemetry + macros
// Test procedure: see NOTES.md
// ============================================================================

#include <Arduino.h>
#include <SPI.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ST7789.h>
#include <Keypad.h>
#include <string.h>
#include <stdlib.h>
#include <limits.h>

// ============================================================================
// DISPLAY PANEL CONFIG
// ============================================================================
// 0 = placeholder panel (240x240, e.g. old 1.54" board-st7789 sim)
// 1 = real 2.25" ST7789 76x284 panel -> 284x76 landscape
//
// The Wokwi simulation uses the custom chip chip-st7789 (chips/st7789.chip.*),
// which matches Adafruit_ST7789::init(76, 284) + setRotation(1) exactly.
#define PANEL_76X284 1

#if PANEL_76X284
  #define PANEL_W 76
  #define PANEL_H 284
#else
  #define PANEL_W 240
  #define PANEL_H 240
#endif

// The 2.25" 76x284 module (LaskaKit LA141117) has the backlight tied to VCC
// internally; BL must be pulled to GND to turn it on (active LOW).
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
// ST7789 Display (hardware SPI: default ESP32 VSPI = SCK 18 / MOSI 23)
#define TFT_CS   5   // back on D5 (GPIO13 is needed by the 3x4 keypad matrix)
#define TFT_DC   16  // Data/Command
#define TFT_RST  4   // Reset
#define TFT_MOSI 23  // SPI MOSI (SDA)
#define TFT_SCK  18  // SPI Clock (SCL)
#define TFT_BL   17  // Backlight

// Keypad Matrix 3x4 (Rows = Outputs, Cols = Inputs with INPUT_PULLUP)
// 3 rows / 4 columns: the old 4 row lines became columns and vice versa.
byte ROW_PINS[3] = {22, 12, 13};
byte COL_PINS[4] = {15, 2, 19, 21};

// Encoder 1
#define E1_CLK 32
#define E1_DT  33
#define E1_SW  25

// Encoder 2
#define E2_CLK 26
#define E2_DT  27
#define E2_SW  14

// ============================================================================
// TUNING
// ============================================================================
#define KEYPAD_SCAN_MS          10
#define DISPLAY_REFRESH_MS      100
#define KEY_DEBOUNCE_MS         20
#define ENC_SW_DEBOUNCE_MS      30
#define ENC_STEPS_PER_DETENT    4   // use 2 for encoders that click twice per turn

// Serial-driven hardware bring-up (B/C/R/I/P/E/K commands).
#define BRINGUP 1

// ============================================================================
// GLOBAL OBJECTS
// ============================================================================
Adafruit_ST7789 tft = Adafruit_ST7789(TFT_CS, TFT_DC, TFT_RST);

char keys[3][4] = {
  {'1', '2', '3', '4'},
  {'5', '6', '7', '8'},
  {'9', '*', '0', '#'}
};

Keypad keypad = Keypad(makeKeymap((char *)keys), ROW_PINS, COL_PINS, 3, 4);

// ============================================================================
// MACRO MAP (profile -> action sent to the PC)
// Row/column order matches the `keys` matrix above.
// Action IDs are executed by the PC app; the firmware only reports them.
// ============================================================================
#define PROFILE_COUNT 3

struct ProfileMap {
  const char *name;              // <= 5 chars for the compact 284x76 layout
  const char *action[3][4];
};

static const ProfileMap PROFILES[PROFILE_COUNT] = {
  {
    "EDIT",
    {
      {"app:spotify", "redo",     "save",    "copy"},
      {"cut",         "paste",    "find",    "replace"},
      {"select_all",  "close",    "delete",  "enter"}
    }
  },
  {
    "MEDIA",
    {
      {"play",       "stop",       "next",     "prev"},
      {"vol_up",     "vol_down",   "mute",     "rewind"},
      {"forward",    "fullscreen", "subtitles","snapshot"}
    }
  },
  {
    "CODE",
    {
      {"build",      "run",        "debug",    "step_over"},
      {"step_in",    "step_out",   "breakpoint","continue"},
      {"stop",       "terminal",   "format",   "commit"}
    }
  }
};

// ============================================================================
// ENCODER STATE (full quadrature decoder)
// ============================================================================
struct EncoderState {
  uint8_t clkPin;
  uint8_t dtPin;
  uint8_t swPin;
  uint8_t lastState;
  int8_t accum;
  int32_t position;
  uint8_t swLastRaw;
  uint8_t swStable;
  unsigned long swChangeMs;
};

// Index = (previous_state << 2) | current_state, state = (CLK << 1) | DT.
// +1 = clockwise, -1 = counter-clockwise, 0 = no/invalid transition.
static const int8_t ENC_TABLE[16] = {
   0, -1,  1,  0,
   1,  0,  0, -1,
  -1,  0,  0,  1,
   0,  1, -1,  0
};

EncoderState enc1 = {E1_CLK, E1_DT, E1_SW, 0, 0, 0, HIGH, HIGH, 0};
EncoderState enc2 = {E2_CLK, E2_DT, E2_SW, 0, 0, 0, HIGH, HIGH, 0};

// ============================================================================
// RUNTIME STATE
// ============================================================================
char lastPressedKey = 0;

int activeProfile  = 0;            // index into PROFILES[]
int telemetryCpu   = -1;
int telemetryGpu   = -1;
int telemetryRam   = -1;
int telemetryTemp  = INT_MIN;

// Page names shown on the display. Defaults come from PROFILES[], but the PC
// host app can rename them at runtime with the "N:<n>:<name>" command.
char profileNames[PROFILE_COUNT][10];

bool compactLayout = false;

unsigned long lastKeypadRead    = 0;
unsigned long lastDisplayUpdate = 0;

// display test text (command H:<text>): shown for TEST_HOLD_MS, then UI returns
char          testText[32]     = "";
bool          testActive       = false;
unsigned long testHoldUntil    = 0;

// Wallpaper: raw big-endian RGB565 frame (284x76 = 43 168 bytes), filled by
// the W:<...> serial transfer from the PC host app.
#define WP_BYTES (PANEL_W * PANEL_H * 2)
static uint16_t wpBuffer[WP_BYTES / 2];
static uint32_t wpTotal   = 0;      // expected payload bytes
static uint32_t wpRecv    = 0;      // payload bytes decoded so far
static bool     wpActive  = false;  // buffer holds a valid frame

// Accent key-flash (theme colour pushed by the PC as A:RRGGBB).
static uint16_t accentColor = 0xA855F7;
static bool          flashActive = false;
static unsigned long flashStart  = 0;
static uint8_t       flashFrame  = 0;
static char          flashKeyChar = ' ';

#if BRINGUP
bool          blinkEnabled = false;
bool          blinkOn      = false;
unsigned long blinkMs      = 0;
bool          rawEcho      = false;
unsigned long rawMs        = 0;
bool          invertOn     = false;
#endif

// ============================================================================
// FUNCTION PROTOTYPES
// ============================================================================
void initDisplay(void);
void drawStaticUI(void);
void updateDisplayValues(void);
void handleKeypad(void);
void handleWallpaper(const char *payload);
bool updateEncoder(EncoderState &enc, const char *name);
void pollSerial(void);
void handleCommand(const char *line);
void switchProfile(int delta);
void sendProfile(void);
bool keyToRowCol(char key, uint8_t &row, uint8_t &col);
void drawFlashBorder(uint16_t color);
void clearFlashBorder(void);
uint16_t dim565(uint16_t color, uint8_t num, uint8_t den);
void drawField(uint8_t id, int16_t x, int16_t y, int16_t w, int16_t h,
               const char *text, uint16_t color, uint8_t size);
void resetFieldCache(void);
void showDisplayText(const char *text);

// ============================================================================
// SETUP
// ============================================================================
void setup() {
  Serial.begin(115200);
  Serial.println(F("=== MACRO KEYPAD FIRMWARE ==="));
  Serial.println(F("ESP32 DevKit v1 | ST7789 | 4x3 Keypad | 2x Encoders"));

  pinMode(TFT_BL, OUTPUT);
  digitalWrite(TFT_BL, TFT_BL_ON);

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

  keypad.setDebounceTime(KEY_DEBOUNCE_MS);

  initDisplay();
  drawStaticUI();

  Serial.println(F("--- READY ---"));
  Serial.print(F("Panel: "));
  Serial.print(tft.width());
  Serial.print('x');
  Serial.println(tft.height());
  sendProfile();
  Serial.println(F("Send 'T:CPU=..;GPU=..;RAM=..' or 'P:n' to test telemetry/profile."));
#if BRINGUP
  Serial.println(F("[BRINGUP] B=blink BL | C:r|g|b|w|k=fill | R:0-3=rot | I=reinit | X:<pin>=probe | E=raw enc | K=matrix"));
#endif
}

// ============================================================================
// MAIN LOOP (non-blocking)
// ============================================================================
void loop() {
#if BRINGUP
  if (blinkEnabled && millis() - blinkMs >= 500) {
    blinkMs = millis();
    blinkOn = !blinkOn;
    digitalWrite(TFT_BL, blinkOn ? TFT_BL_ON : TFT_BL_OFF);
  }
  if (rawEcho && millis() - rawMs >= 250) {
    rawMs = millis();
    Serial.printf("RAW E1 c=%d d=%d s=%d | E2 c=%d d=%d s=%d\n",
                  digitalRead(E1_CLK), digitalRead(E1_DT), digitalRead(E1_SW),
                  digitalRead(E2_CLK), digitalRead(E2_DT), digitalRead(E2_SW));
  }
#endif
  if (flashActive) {
    unsigned long elapsed = millis() - flashStart;
    uint8_t frame = elapsed >= 220 ? 3 : (elapsed >= 150 ? 2
                    : (elapsed >= 75 ? 1 : 0));
    if (frame != flashFrame) {
      flashFrame = frame;
      if (frame == 0) {
        drawFlashBorder(accentColor);
      } else if (frame == 1) {
        drawFlashBorder(dim565(accentColor, 4, 5));
      } else if (frame == 2) {
        drawFlashBorder(dim565(accentColor, 2, 5));
      } else {
        clearFlashBorder();
        flashActive = false;
      }
    }
  }
  updateEncoder(enc1, "ENC1");
  if (updateEncoder(enc2, "ENC2")) {
    Serial.println(F("MACRO:custom"));
  }

  pollSerial();

  unsigned long now = millis();

  if (now - lastKeypadRead >= KEYPAD_SCAN_MS) {
    handleKeypad();
    lastKeypadRead = now;
  }

  if (testActive) {
    if ((long)(millis() - testHoldUntil) >= 0) {
      testActive = false;
      drawStaticUI();
      lastDisplayUpdate = 0;   // force immediate refresh on next loop
    }
  } else if (now - lastDisplayUpdate >= DISPLAY_REFRESH_MS) {
    updateDisplayValues();
    lastDisplayUpdate = now;
  }
}

// ============================================================================
// DISPLAY
// ============================================================================
void initDisplay(void) {
  tft.init(PANEL_W, PANEL_H);
  tft.invertDisplay(false);        // this 76x284 panel needs inversion OFF
  tft.setRotation(1);              // landscape: long edge horizontal
  tft.fillScreen(ST77XX_BLACK);
  tft.setTextWrap(false);

  compactLayout = (tft.height() < 100);
}

void restoreStrip(int16_t x, int16_t y, int16_t w, int16_t h) {
  if (wpActive) {
    for (int16_t row = 0; row < h; row++) {
      tft.drawRGBBitmap(x, y + row,
                        wpBuffer + (y + row) * tft.width() + x, w, 1);
    }
  } else {
    tft.fillRect(x, y, w, h, ST77XX_BLACK);
  }
}

// Key-flash badge lives in the free bottom-right corner of both layouts, so
// restoring it never paints over header/labels.
#define FLASH_BOX_W 40
#define FLASH_BOX_H 12

void flashBoxOrigin(int16_t &x, int16_t &y) {
  x = tft.width() - FLASH_BOX_W - 2;
  y = tft.height() - FLASH_BOX_H - 2;
}

void drawFlashBorder(uint16_t color) {
  int16_t x, y;
  flashBoxOrigin(x, y);
  tft.fillRect(x, y, FLASH_BOX_W, FLASH_BOX_H, color);
  tft.setTextSize(1);
  tft.setTextColor(ST77XX_BLACK);
  tft.setCursor(x + 4, y + 3);
  tft.print(F("KEY "));
  tft.print(flashKeyChar);
}

void clearFlashBorder(void) {
  int16_t x, y;
  flashBoxOrigin(x, y);
  restoreStrip(x, y, FLASH_BOX_W, FLASH_BOX_H);
}

uint16_t dim565(uint16_t color, uint8_t num, uint8_t den) {
  uint8_t r = ((color >> 11) & 0x1F) * num / den;
  uint8_t g = ((color >> 5) & 0x3F) * num / den;
  uint8_t b = (color & 0x1F) * num / den;
  return (r << 11) | (g << 5) | b;
}

void drawStaticUI(void) {
  resetFieldCache();
  if (wpActive) tft.drawRGBBitmap(0, 0, wpBuffer, tft.width(), tft.height());
  else tft.fillScreen(ST77XX_BLACK);

  if (compactLayout) {
    tft.fillRect(0, 0, tft.width(), 12, ST77XX_BLUE);
    tft.setTextSize(1);
    tft.setTextColor(ST77XX_WHITE);
    tft.setCursor(2, 2);
    tft.print(F("MACRO KEYPAD"));

    tft.setTextColor(ST77XX_CYAN);
    tft.setCursor(2, 18);   tft.print(F("E1"));
    tft.setCursor(80, 18);  tft.print(F("E2"));
    tft.setCursor(158, 18); tft.print(F("KEY"));
    tft.setCursor(2, 36);   tft.print(F("PROF"));
    tft.setCursor(2, 54);   tft.print(F("PC"));
  } else {
    tft.fillRect(0, 0, tft.width(), 26, ST77XX_BLUE);
    tft.setTextSize(2);
    tft.setTextColor(ST77XX_WHITE);
    int16_t x = (tft.width() - (12 * 12)) / 2;
    if (x < 0) x = 0;
    tft.setCursor(x, 5);
    tft.print(F("MACRO KEYPAD"));

    const char *labels[5] = {"Profile", "Encoder 1", "Encoder 2", "Last key", "PC"};
    tft.setTextSize(1);
    tft.setTextColor(ST77XX_CYAN);
    for (uint8_t i = 0; i < 5; i++) {
      tft.setCursor(4, 30 + i * 20 + 6);
      tft.print(labels[i]);
      tft.print(':');
    }
  }
}

void updateDisplayValues(void) {
  char buf[32];
  char pc[32];
  char keyStr[2] = {(char)(lastPressedKey ? lastPressedKey : '-'), '\0'};

  snprintf(pc, sizeof(pc), "waiting");
  if (telemetryCpu >= 0 || telemetryGpu >= 0 || telemetryRam >= 0
      || telemetryTemp != INT_MIN) {
    int n = 0;
    pc[0] = '\0';
    if (telemetryCpu >= 0)
      n += snprintf(pc + n, sizeof(pc) - n, "C%d ", telemetryCpu);
    if (telemetryGpu >= 0 && n < (int)sizeof(pc))
      n += snprintf(pc + n, sizeof(pc) - n, "G%d ", telemetryGpu);
    if (telemetryRam >= 0 && n < (int)sizeof(pc))
      n += snprintf(pc + n, sizeof(pc) - n, "R%d ", telemetryRam);
    if (telemetryTemp != INT_MIN && n < (int)sizeof(pc))
      snprintf(pc + n, sizeof(pc) - n, "%dC", telemetryTemp);
  }

  const char *profileStr = profileNames[activeProfile];

  snprintf(buf, sizeof(buf), "%ld", (long)enc1.position);
  char enc1Str[32];
  strncpy(enc1Str, buf, sizeof(enc1Str) - 1);
  enc1Str[sizeof(enc1Str) - 1] = '\0';

  snprintf(buf, sizeof(buf), "%ld", (long)enc2.position);
  char enc2Str[32];
  strncpy(enc2Str, buf, sizeof(enc2Str) - 1);
  enc2Str[sizeof(enc2Str) - 1] = '\0';

  if (compactLayout) {
    drawField(1, 22, 14, 50, 18, enc1Str, ST77XX_GREEN, 2);
    drawField(2, 100, 14, 50, 18, enc2Str, ST77XX_GREEN, 2);
    drawField(3, 188, 14, 40, 18, keyStr, ST77XX_YELLOW, 2);
    drawField(0, 32, 32, 60, 18, profileStr, ST77XX_MAGENTA, 2);
    drawField(4, 24, 52, tft.width() - 24, 10, pc, ST77XX_CYAN, 1);
  } else {
    drawField(0, 84, 30,  tft.width() - 84, 18, profileStr, ST77XX_MAGENTA, 2);
    drawField(1, 84, 50,  tft.width() - 84, 18, enc1Str, ST77XX_GREEN, 2);
    drawField(2, 84, 70,  tft.width() - 84, 18, enc2Str, ST77XX_GREEN, 2);
    drawField(3, 84, 90,  tft.width() - 84, 18, keyStr, ST77XX_YELLOW, 2);
    drawField(4, 84, 110, tft.width() - 84, 10, pc, ST77XX_CYAN, 1);
  }
}

// ============================================================================
// ENCODER HANDLING
// ============================================================================
bool updateEncoder(EncoderState &enc, const char *name) {
  bool pressed = false;
  uint8_t state = (uint8_t)((digitalRead(enc.clkPin) << 1) | digitalRead(enc.dtPin));

  if (state != enc.lastState) {
    enc.accum += ENC_TABLE[(enc.lastState << 2) | state];
    enc.lastState = state;

    if (enc.accum >= ENC_STEPS_PER_DETENT) {
      enc.accum = 0;
      enc.position++;
      Serial.print(name);
      Serial.print(':');
      Serial.println(enc.position);
    } else if (enc.accum <= -ENC_STEPS_PER_DETENT) {
      enc.accum = 0;
      enc.position--;
      Serial.print(name);
      Serial.print(':');
      Serial.println(enc.position);
    }
  }

  uint8_t sw = (uint8_t)digitalRead(enc.swPin);
  if (sw != enc.swLastRaw) {
    enc.swLastRaw = sw;
    enc.swChangeMs = millis();
  }
  if ((millis() - enc.swChangeMs) >= ENC_SW_DEBOUNCE_MS && sw != enc.swStable) {
    enc.swStable = sw;
    if (sw == LOW) {
      Serial.print(name);
      Serial.println(F("_SW:1"));
      pressed = true;
    }
  }

  return pressed;
}

// ============================================================================
// KEYPAD + PROFILE MAPPING
// ============================================================================
bool keyToRowCol(char key, uint8_t &row, uint8_t &col) {
  for (uint8_t r = 0; r < 3; r++) {
    for (uint8_t c = 0; c < 4; c++) {
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

void switchProfile(int delta) {
  activeProfile = (activeProfile + delta) % PROFILE_COUNT;
  if (activeProfile < 0) activeProfile += PROFILE_COUNT;
  sendProfile();
}

void handleKeypad(void) {
  char key = keypad.getKey();
  if (key == NO_KEY) return;

  lastPressedKey = key;

  Serial.print(F("KEY:"));
  Serial.println(key);

  flashActive = true;
  flashStart = millis();
  flashFrame = 0;
  flashKeyChar = key;
  drawFlashBorder(accentColor);

  uint8_t row = 0;
  uint8_t col = 0;
  if (keyToRowCol(key, row, col)) {
    Serial.print(F("MACRO:"));
    Serial.println(PROFILES[activeProfile].action[row][col]);
  }
}

// ============================================================================
// SERIAL: PC -> ESP32 telemetry
// Protocol (line based, '\n' terminated):
//   T:CPU=45;GPU=30;RAM=52;TEMP=52
//   P:2
//   N:2:MEDIA            (rename page 2, shown on the display)
//   H:<text>             (display test text for 5 s)
//   W:B:<bytes>          (wallpaper transfer: begin -> base64 chunks -> W:E)
//   W:<idx>:<base64>     (48 raw RGB565 bytes per chunk, big-endian words)
//   W:E                  (commit frame to the ST7789 and redraw the UI)
//   A:RRGGBB             (theme accent for the key-flash badge)
//   F:<key>              (pulse the accent key badge, e.g. GUI key click)
// ============================================================================
static char rxLine[96];
static uint8_t rxLen = 0;

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
  pinMode(pin, INPUT);
  pinMode(pin, INPUT_PULLUP);   // restore idle state used by encoders/display

  Serial.printf("PROBE %d: pullup=%d pulldown=%d", pin, up, down);
  if (up == 1 && down == 0)       Serial.println(F(" -> floating (nothing connected)"));
  else if (up == 1 && down == 1)  Serial.println(F(" -> tied to 3V3"));
  else if (up == 0 && down == 0)  Serial.println(F(" -> tied to GND"));
  else                            Serial.println(F(" -> unstable"));
}

static void scanMatrix(void) {
  for (uint8_t r = 0; r < 3; r++) {
    pinMode(ROW_PINS[r], OUTPUT);
    digitalWrite(ROW_PINS[r], HIGH);
  }
  for (uint8_t c = 0; c < 4; c++) pinMode(COL_PINS[c], INPUT_PULLUP);
  delay(2);

  Serial.print(F("MATRIX rows 22/12/13, cols 15/2/19/21:"));
  for (uint8_t r = 0; r < 3; r++) {
    digitalWrite(ROW_PINS[r], LOW);
    delayMicroseconds(200);
    Serial.print(' ');
    for (uint8_t c = 0; c < 4; c++) Serial.print(digitalRead(COL_PINS[c]));
    digitalWrite(ROW_PINS[r], HIGH);
  }
  Serial.println(F("   (0=pressed/short, 1=idle)"));
}
#endif

// ============================================================================
// WALLPAPER (W:B:<bytes> / W:<idx>:<base64> / W:E)
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
    if (wpTotal == 0 || wpTotal > WP_BYTES) {
      wpTotal = 0;
      Serial.println(F("WP:BAD"));
    }
    return;
  }
  if (payload[0] == 'E' && payload[1] == '\0') {
    if (wpTotal == 0 || wpRecv < wpTotal) {
      Serial.printf("WP:TRUNC %lu/%lu\n", (unsigned long)wpRecv,
                    (unsigned long)wpTotal);
      return;
    }
    wpActive = true;
    drawStaticUI();          // draws the frame + header + labels + field cache
    lastDisplayUpdate = 0;
    Serial.printf("WP:OK %lu\n", (unsigned long)wpRecv);
    return;
  }
  const char *sep = strchr(payload, ':');
  if (sep == nullptr || wpTotal == 0) return;
  uint32_t byteOffset = (uint32_t)strtoul(payload, nullptr, 10);
  uint8_t tmp[48];
  int n = b64Decode(sep + 1, tmp, (int)sizeof(tmp));
  if (n <= 0) return;
  // Big-endian byte pairs -> native uint16 color words for Adafruit GFX.
  uint32_t pixel = byteOffset / 2;
  for (int i = 0; i + 1 < n && pixel < WP_BYTES / 2; i += 2) {
    wpBuffer[pixel++] = (uint16_t)((tmp[i] << 8) | tmp[i + 1]);
  }
  if (byteOffset + (uint32_t)n > wpRecv) wpRecv = byteOffset + (uint32_t)n;
}

void handleCommand(const char *line) {
#if BRINGUP
  if (line[0] == 'B' && line[1] == '\0') {
    blinkEnabled = !blinkEnabled;
    if (!blinkEnabled) digitalWrite(TFT_BL, TFT_BL_ON);
    Serial.print(F("BLINK:"));
    Serial.println(blinkEnabled ? 1 : 0);
    return;
  }
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
      wpActive = false;      // solid fill replaces any wallpaper
      tft.fillScreen(col);
      Serial.printf("FILL:%c\n", k);
    }
    return;
  }
  if (line[0] == 'R' && line[1] == ':') {
    int n = atoi(line + 2);
    if (n < 0 || n > 3) { Serial.println(F("R:0-3")); return; }
    tft.setRotation(n);
    compactLayout = (tft.height() < 100);
    drawStaticUI();
    lastDisplayUpdate = 0;
    Serial.printf("ROT:%d %dx%d\n", n, tft.width(), tft.height());
    return;
  }
  if (line[0] == 'I' && line[1] == '\0') {
    initDisplay();
    drawStaticUI();
    lastDisplayUpdate = 0;
    Serial.printf("REINIT %dx%d\n", tft.width(), tft.height());
    return;
  }
  if (line[0] == 'X' && line[1] == ':') {
    probePin(atoi(line + 2));
    return;
  }
  if (line[0] == 'V' && line[1] == '\0') {
    invertOn = !invertOn;
    tft.invertDisplay(invertOn);
    Serial.print(F("INVERT:"));
    Serial.println(invertOn ? 1 : 0);
    return;
  }
  if (line[0] == 'E' && line[1] == '\0') {
    rawEcho = !rawEcho;
    Serial.print(F("RAWMODE:"));
    Serial.println(rawEcho ? 1 : 0);
    return;
  }
  if (line[0] == 'K' && line[1] == '\0') {
    scanMatrix();
    return;
  }
  if (line[0] == 'Q' && line[1] == '\0') {
    static const uint8_t pins[] = {5, 16, 4, 18, 23, 17, 32, 33, 25, 26, 27, 14};
    for (uint8_t i = 0; i < sizeof(pins); i++) probePin(pins[i]);
    return;
  }
#endif
  if (line[0] == 'A' && line[1] == ':') {
    if (strlen(line) >= 8) {
      unsigned long v = strtoul(line + 2, nullptr, 16);
      accentColor = tft.color565((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF);
      Serial.print(F("ACCENT:"));
      Serial.println(line + 2);
    }
    return;
  }
  if (line[0] == 'F' && line[1] == ':') {
    if (line[2]) flashKeyChar = line[2];
    flashActive = true;
    flashStart = millis();
    flashFrame = 0;
    drawFlashBorder(accentColor);
    return;
  }
  if (line[0] == 'S' && line[1] == '\0') {
    static const uint8_t pins[] = {4, 5, 13, 14, 15, 16, 17, 18, 19,
                                   21, 22, 23, 25, 26, 27, 32, 33};
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
    drawStaticUI();
    lastDisplayUpdate = 0;
    Serial.println(F("SWEEP DONE"));
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
  } else if (line[0] == 'P' && line[1] == ':') {
    int n = atoi(line + 2);
    if (n >= 1) {
      activeProfile = (n - 1) % PROFILE_COUNT;
      sendProfile();
    }
  } else if (line[0] == 'N' && line[1] == ':') {
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
      lastDisplayUpdate = 0;   // force a redraw with the new name
    }
  } else if (line[0] == 'H' && line[1] == ':') {
    strncpy(testText, line + 2, sizeof(testText) - 1);
    testText[sizeof(testText) - 1] = '\0';
    showDisplayText(testText);
    testActive = true;
    testHoldUntil = millis() + 5000;
    Serial.print(F("DISPLAY:"));
    Serial.println(testText);
  } else if (line[0] == 'W' && line[1] == ':') {
    handleWallpaper(line + 2);
  }
}

// ============================================================================
// DISPLAY TEST TEXT (H:<text>)
// ============================================================================
void showDisplayText(const char *text) {
  tft.fillScreen(ST77XX_BLACK);
  tft.drawRect(0, 0, tft.width(), tft.height(), ST77XX_GREEN);

  int len = strlen(text);
  if (len == 0) return;

  uint8_t size = 2;                      // auto-shrink to fit the panel width
  while (size > 1 && (6 * size * len) > tft.width() - 8) size--;

  tft.setTextSize(size);
  tft.setTextColor(ST77XX_YELLOW);
  int16_t x = (tft.width()  - 6 * size * len) / 2;
  int16_t y = (tft.height() - 8 * size) / 2;
  if (x < 2) x = 2;
  if (y < 2) y = 2;
  tft.setCursor(x, y);
  tft.print(text);
}

// ============================================================================
// DISPLAY HELPERS (only redraw when the text changes)
// ============================================================================
static char fieldCache[8][24];
static bool fieldValid[8];

void resetFieldCache(void) {
  for (uint8_t i = 0; i < 8; i++) {
    fieldValid[i] = false;
    fieldCache[i][0] = '\0';
  }
}

void drawField(uint8_t id, int16_t x, int16_t y, int16_t w, int16_t h,
               const char *text, uint16_t color, uint8_t size) {
  if (id >= 8 || w <= 0 || h <= 0) return;

  if (fieldValid[id] && strncmp(fieldCache[id], text, sizeof(fieldCache[id]) - 1) == 0)
    return;

  if (wpActive) {   // repaint the wallpaper region instead of a black box
    for (int16_t row = 0; row < h; row++) {
      tft.drawRGBBitmap(x, y + row,
                        wpBuffer + (y + row) * tft.width() + x, w, 1);
    }
  } else {
    tft.fillRect(x, y, w, h, ST77XX_BLACK);
  }
  tft.setTextSize(size);
  tft.setTextColor(color);
  tft.setCursor(x, y);
  tft.print(text);

  strncpy(fieldCache[id], text, sizeof(fieldCache[id]) - 1);
  fieldCache[id][sizeof(fieldCache[id]) - 1] = '\0';
  fieldValid[id] = true;
}
