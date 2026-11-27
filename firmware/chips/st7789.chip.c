#include "wokwi-api.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define TFT_COLSTART 82u
#define TFT_ROWSTART 18u

#define CMD_SWRESET 0x01
#define CMD_SLPOUT  0x11
#define CMD_NORON   0x13
#define CMD_INVOFF  0x20
#define CMD_INVON   0x21
#define CMD_DISPOFF 0x28
#define CMD_DISPON  0x29
#define CMD_CASET   0x2A
#define CMD_RASET   0x2B
#define CMD_RAMWR   0x2C
#define CMD_MADCTL  0x36
#define CMD_COLMOD  0x3A
#define CMD_RAMWRC  0x3C

#define MAD_MY 0x80u
#define MAD_MX 0x40u
#define MAD_MV 0x20u

typedef enum {
  MODE_NONE,
  MODE_CASET,
  MODE_RASET,
  MODE_MADCTL,
  MODE_COLMOD,
  MODE_PIXEL
} rx_mode_t;

typedef struct {
  pin_t cs;
  pin_t dc;
  pin_t rst;
  spi_dev_t spi;
  uint8_t spi_buf[1];
  buffer_t fb;
  uint32_t fb_w;
  uint32_t fb_h;
  rx_mode_t mode;
  uint16_t xs;
  uint16_t xe;
  uint16_t ys;
  uint16_t ye;
  uint32_t case_len;
  uint32_t px_idx;
  uint8_t madctl;
  uint8_t colmod;
  uint8_t acc[4];
  uint8_t acc_n;
  uint8_t px[3];
  uint8_t px_n;
} chip_state_t;

static uint8_t zero_chunk[1024];

static uint8_t expand5(uint8_t v) {
  return (uint8_t)((v << 3) | (v >> 2));
}

static uint8_t expand6(uint8_t v) {
  return (uint8_t)((v << 2) | (v >> 4));
}

static void fb_clear(chip_state_t *chip) {
  memset(zero_chunk, 0, sizeof(zero_chunk));
  uint32_t total = chip->fb_w * chip->fb_h * 4u;
  for (uint32_t off = 0; off < total; off += sizeof(zero_chunk)) {
    uint32_t len = total - off;
    if (len > sizeof(zero_chunk))
      len = sizeof(zero_chunk);
    buffer_write(chip->fb, off, zero_chunk, len);
  }
}

static void reset_chip(chip_state_t *chip) {
  chip->mode = MODE_NONE;
  chip->xs = 0;
  chip->xe = 239;
  chip->ys = 0;
  chip->ye = 319;
  chip->case_len = 240u;
  chip->px_idx = 0;
  chip->madctl = 0x00u;
  chip->colmod = 0x55u;
  chip->acc_n = 0;
  chip->px_n = 0;
  fb_clear(chip);
}

static void set_case_window(chip_state_t *chip) {
  chip->xs = (uint16_t)((chip->acc[0] << 8) | chip->acc[1]);
  chip->xe = (uint16_t)((chip->acc[2] << 8) | chip->acc[3]);
  chip->px_idx = 0;
  chip->case_len = (chip->xe >= chip->xs) ? (uint32_t)(chip->xe - chip->xs + 1u) : 0u;
}

static void set_row_window(chip_state_t *chip) {
  chip->ys = (uint16_t)((chip->acc[0] << 8) | chip->acc[1]);
  chip->ye = (uint16_t)((chip->acc[2] << 8) | chip->acc[3]);
  chip->px_idx = 0;
}

static void put_pixel(chip_state_t *chip, uint8_t r8, uint8_t g8, uint8_t b8) {
  uint8_t rgba[4] = {r8, g8, b8, 0xFF};

  uint32_t case_idx = chip->px_idx % chip->case_len;
  uint32_t rase_idx = chip->px_idx / chip->case_len;
  chip->px_idx++;

  uint32_t case_val = (uint32_t)chip->xs + case_idx;
  uint32_t rase_val = (uint32_t)chip->ys + rase_idx;

  int32_t dx;
  int32_t dy;

  if (chip->madctl & MAD_MV) {
    // virtual glass has no physical polarity -> MADCTL MX/MY bits must not flip
    dx = (int32_t)(case_val - TFT_ROWSTART);
    dy = (int32_t)(rase_val - TFT_COLSTART);
  } else {
    dx = (int32_t)(case_val - TFT_COLSTART);
    dy = (int32_t)(rase_val - TFT_ROWSTART);
    if (chip->madctl & MAD_MX)
      dx = (int32_t)chip->fb_w - 1 - dx;
    if (chip->madctl & MAD_MY)
      dy = (int32_t)chip->fb_h - 1 - dy;
  }

  if (chip->case_len == 0u)
    return;
  if (dx < 0 || dy < 0 || (uint32_t)dx >= chip->fb_w || (uint32_t)dy >= chip->fb_h)
    return;

  buffer_write(chip->fb, ((uint32_t)dy * chip->fb_w + (uint32_t)dx) * 4u, rgba, 4);
}

static void process_data(chip_state_t *chip, uint8_t b) {
  switch (chip->mode) {
    case MODE_CASET:
    case MODE_RASET:
      if (chip->acc_n < 4u) {
        chip->acc[chip->acc_n++] = b;
        if (chip->acc_n == 4u) {
          if (chip->mode == MODE_CASET)
            set_case_window(chip);
          else
            set_row_window(chip);
          chip->mode = MODE_NONE;
        }
      }
      break;

    case MODE_MADCTL:
      chip->madctl = b;
      chip->mode = MODE_NONE;
      break;

    case MODE_COLMOD:
      chip->colmod = b;
      chip->mode = MODE_NONE;
      break;

    case MODE_PIXEL: {
      uint8_t bpp = (chip->colmod == 0x66u || chip->colmod == 0x77u) ? 3u : 2u;
      chip->px[chip->px_n] = b;
      chip->px_n++;
      if (chip->px_n == bpp) {
        chip->px_n = 0;
        if (bpp == 2u) {
          uint16_t c565 = (uint16_t)((chip->px[0] << 8) | chip->px[1]);
          put_pixel(chip, expand5(c565 >> 11), expand6((c565 >> 5) & 0x3F), expand5(c565 & 0x1F));
        } else {
          put_pixel(chip, expand6(chip->px[0] >> 2), expand6(chip->px[1] >> 2), expand6(chip->px[2] >> 2));
        }
      }
      break;
    }

    case MODE_NONE:
    default:
      break;
  }
}

static void process_command(chip_state_t *chip, uint8_t cmd) {
  chip->acc_n = 0;
  chip->px_n = 0;

  switch (cmd) {
    case CMD_SWRESET:
      reset_chip(chip);
      break;

    case CMD_SLPOUT:
    case CMD_NORON:
    case CMD_INVOFF:
    case CMD_INVON:
    case CMD_DISPOFF:
    case CMD_DISPON:
      chip->mode = MODE_NONE;
      break;

    case CMD_CASET:
      chip->mode = MODE_CASET;
      break;

    case CMD_RASET:
      chip->mode = MODE_RASET;
      break;

    case CMD_RAMWR:
    case CMD_RAMWRC:
      chip->px_idx = 0;
      chip->px_n = 0;
      chip->mode = MODE_PIXEL;
      break;

    case CMD_MADCTL:
      chip->mode = MODE_MADCTL;
      break;

    case CMD_COLMOD:
      chip->mode = MODE_COLMOD;
      break;

    default:
      chip->mode = MODE_NONE;
      break;
  }
}

static void chip_pin_change(void *user_data, pin_t pin, uint32_t value) {
  chip_state_t *chip = (chip_state_t *)user_data;
  if (pin == chip->rst) {
    if (value == LOW)
      reset_chip(chip);
    return;
  }
  if (pin != chip->cs)
    return;

  if (value == LOW) {
    spi_start(chip->spi, chip->spi_buf, 1);
  } else {
    spi_stop(chip->spi);
  }
}

static void chip_spi_done(void *user_data, uint8_t *buffer, uint32_t count) {
  chip_state_t *chip = (chip_state_t *)user_data;
  if (count == 0)
    return;

  uint8_t byte = buffer[0];
  if (pin_read(chip->dc) == HIGH)
    process_data(chip, byte);
  else
    process_command(chip, byte);

  if (pin_read(chip->cs) == LOW)
    spi_start(chip->spi, chip->spi_buf, 1);
}

void chip_init(void) {
  chip_state_t *chip = malloc(sizeof(chip_state_t));
  memset(chip, 0, sizeof(chip_state_t));

  chip->fb_w = 284u;
  chip->fb_h = 76u;
  chip->fb = framebuffer_init(&chip->fb_w, &chip->fb_h);

  pin_t cs = pin_init("CS", INPUT_PULLUP);
  pin_t dc = pin_init("DC", INPUT_PULLUP);
  pin_t rst = pin_init("RES", INPUT_PULLUP);
  pin_t sck = pin_init("SCL", INPUT);
  pin_t mosi = pin_init("SDA", INPUT);
  pin_init("BLK", INPUT);
  pin_init("VCC", INPUT);
  pin_init("GND", INPUT);
  chip->cs = cs;
  chip->dc = dc;
  chip->rst = rst;

  const spi_config_t spi_config = {
    .sck = sck,
    .mosi = mosi,
    .miso = NO_PIN,
    .mode = 0,
    .done = chip_spi_done,
    .user_data = chip,
  };
  chip->spi = spi_init(&spi_config);

  pin_watch_config_t watch = {
    .user_data = chip,
    .edge = BOTH,
    .pin_change = chip_pin_change,
  };
  pin_watch(chip->cs, &watch);
  pin_watch(chip->rst, &watch);
  reset_chip(chip);

  printf("chip-st7789: 284x76 framebuffer display ready\n");
}
