// HW test - ST7789 76x284 + 2x EC11
// Arduino IDE: Boards Manager -> esp32; Library Manager -> "Adafruit ST7735 and ST7789 Library" (+ GFX)
// Board: "ESP32 Dev Module" (or uPesy WROOM), port COM7

#include <SPI.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ST7789.h>

#define TFT_CS   5
#define TFT_DC   16
#define TFT_RST  4
#define TFT_BL   17

#define E1_CLK 32
#define E1_DT  33
#define E1_SW  25
#define E2_CLK 26
#define E2_DT  27
#define E2_SW  14

Adafruit_ST7789 tft(TFT_CS, TFT_DC, TFT_RST);

// quadrature table [prev<<2 | cur]
static const int8_t ENC_TABLE[16] = {
   0, -1,  1,  0,
   1,  0,  0, -1,
  -1,  0,  0,  1,
   0,  1, -1,  0
};

long e1pos = 0, e2pos = 0;
uint8_t e1last, e2last;
uint8_t e1swLast = 1, e2swLast = 1;

void row(int x, int y, int w, const char *txt, uint16_t color, uint8_t size) {
  tft.fillRect(x, y, w, size * 8 + 2, ST77XX_BLACK);
  tft.setTextSize(size);
  tft.setTextColor(color);
  tft.setCursor(x, y);
  tft.print(txt);
}

void setup() {
  Serial.begin(115200);
  delay(400);
  Serial.println();
  Serial.println("=== HW TEST: display + encoders ===");

  pinMode(TFT_BL, OUTPUT);
  digitalWrite(TFT_BL, LOW);    // backlight ON (module is active LOW)

  SPI.begin(18, -1, 23, 5);     // SCK / MOSI of the display

  pinMode(E1_CLK, INPUT_PULLUP);
  pinMode(E1_DT,  INPUT_PULLUP);
  pinMode(E1_SW,  INPUT_PULLUP);
  pinMode(E2_CLK, INPUT_PULLUP);
  pinMode(E2_DT,  INPUT_PULLUP);
  pinMode(E2_SW,  INPUT_PULLUP);

  Serial.print("Panel init... ");
  tft.init(76, 284);
  tft.setRotation(1);
  tft.setTextWrap(false);
  Serial.print("OK, ");
  Serial.print(tft.width());
  Serial.print("x");
  Serial.println(tft.height());

  tft.fillScreen(ST77XX_RED);   Serial.println("RED");   delay(700);
  tft.fillScreen(ST77XX_GREEN); Serial.println("GREEN"); delay(700);
  tft.fillScreen(ST77XX_BLUE);  Serial.println("BLUE");  delay(700);
  tft.fillScreen(ST77XX_WHITE); Serial.println("WHITE"); delay(700);
  tft.fillScreen(ST77XX_BLACK);

  tft.setTextSize(1);
  tft.setTextColor(ST77XX_CYAN, ST77XX_BLACK);
  tft.setCursor(2, 2);
  tft.print(F("HW TEST OK - otacej enkodery"));

  e1last = (uint8_t)((digitalRead(E1_CLK) << 1) | digitalRead(E1_DT));
  e2last = (uint8_t)((digitalRead(E2_CLK) << 1) | digitalRead(E2_DT));

  Serial.println("Hotovo. Otacej enkodery, mackej SW. Sleduj displej i Serial Monitor.");
}

void loop() {
  uint8_t s1 = (uint8_t)((digitalRead(E1_CLK) << 1) | digitalRead(E1_DT));
  if (s1 != e1last) {
    int8_t d = ENC_TABLE[(e1last << 2) | s1];
    if (d) { e1pos += d; Serial.print("ENC1:"); Serial.println(e1pos); }
    e1last = s1;
  }
  uint8_t s2 = (uint8_t)((digitalRead(E2_CLK) << 1) | digitalRead(E2_DT));
  if (s2 != e2last) {
    int8_t d = ENC_TABLE[(e2last << 2) | s2];
    if (d) { e2pos += d; Serial.print("ENC2:"); Serial.println(e2pos); }
    e2last = s2;
  }

  uint8_t sw1 = (uint8_t)digitalRead(E1_SW);
  uint8_t sw2 = (uint8_t)digitalRead(E2_SW);
  if (sw1 != e1swLast) {
    e1swLast = sw1;
    Serial.print("SW1:"); Serial.println(sw1 ? 1 : 0);
  }
  if (sw2 != e2swLast) {
    e2swLast = sw2;
    Serial.print("SW2:"); Serial.println(sw2 ? 1 : 0);
  }

  static unsigned long lastDraw = 0;
  if (millis() - lastDraw < 150) return;
  lastDraw = millis();

  char b[32];
  snprintf(b, sizeof(b), "E1: %ld", e1pos);
  row(4, 16, 140, b, ST77XX_GREEN, 2);
  snprintf(b, sizeof(b), "E2: %ld", e2pos);
  row(150, 16, 130, b, ST77XX_GREEN, 2);

  snprintf(b, sizeof(b), "SW1:%d  SW2:%d", sw1, sw2);
  row(4, 38, 280, b, ST77XX_YELLOW, 1);

  snprintf(b, sizeof(b), "raw1 c%d d%d s%d | raw2 c%d d%d s%d",
           digitalRead(E1_CLK), digitalRead(E1_DT), sw1,
           digitalRead(E2_CLK), digitalRead(E2_DT), sw2);
  row(4, 52, 280, b, ST77XX_WHITE, 1);

  Serial.printf("RAW 1:c%d d%d s%d | 2:c%d d%d s%d\n",
                digitalRead(E1_CLK), digitalRead(E1_DT), sw1,
                digitalRead(E2_CLK), digitalRead(E2_DT), sw2);
}
