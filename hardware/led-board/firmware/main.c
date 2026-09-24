/*
 * Boat MFD LED board: the RP2040's firmware.
 *
 * The RP2040 runs the board's four addressable outputs (J7-J10, 12 V WS2815-type strips). It is
 * an I2C target at 0x30 on the board's bus -- the same bus as the PCA9685 (0x40) and the INA226
 * (0x45), reached from the Pi over the RJ45 link -- and the Pi tells it what each output shows:
 * a mode (off, a solid colour, a rainbow), a colour, a brightness, the strip's length. It draws
 * the frames itself, so the link only ever carries a few bytes, and the rainbow keeps moving
 * smoothly whatever the Pi is doing.
 *
 * Registers (8-bit addresses; a write or read runs on through consecutive registers):
 *   0x00-0x01  'L' 'B'                      read-only: identifies the board
 *   0x02-0x03  firmware version, major/minor read-only
 *   0x04       status: bit 0 running        read-only
 *   0x05       frame counter                read-only, wraps at 255
 *   0x08       limit, 0-255 (default 255)   scales every output: the Pi lowers it to hold the
 *                                           board under its current budget
 *   0x10 + 0x10*n, output n = 0..3 (J7..J10):
 *     +0 mode        0 off, 1 solid, 2 rainbow
 *     +1..+3 R G B   the solid colour
 *     +4 brightness  0-255
 *     +5 speed       rainbow: full colour cycles per minute (default 9)
 *     +6 order       the strip's byte order: 0 GRB (WS2812/WS2815), 1 RGB, 2 BRG, 3 RBG, 4 GBR, 5 BGR
 *     +7 flags       bit 0: run the rainbow the other way along the strip
 *     +8..+9 count   pixels on the strip, little-endian, up to 600
 *   0x7E       write 0xB0: restart as a USB drive, to load new firmware (needs USB-C plugged in)
 *   0x7F       write 0x5A: restart
 *
 * A write takes effect when the transfer ends (the I2C stop), so an output's registers change
 * together. Everything starts off: the strips stay dark until the Pi sets them, and the Pi
 * repeats its settings every two seconds, so a restart here recovers on its own.
 *
 * Pins (hardware/led-board/design.py): GPIO0/1 I2C0 SDA/SCL (through the BSS138 level shift),
 * GPIO2-5 the four outputs' data (buffered to 5 V by U7-U10), GPIO6 the status LED (D8).
 */
#include <string.h>

#include "hardware/clocks.h"
#include "hardware/dma.h"
#include "hardware/gpio.h"
#include "hardware/i2c.h"
#include "hardware/pio.h"
#include "hardware/sync.h"
#include "hardware/watchdog.h"
#include "pico/bootrom.h"
#include "pico/i2c_slave.h"
#include "pico/stdlib.h"
#include "ws2812.pio.h"

#define VERSION_MAJOR 1
#define VERSION_MINOR 0

#define I2C_ADDRESS 0x30
#define PIN_SDA 0
#define PIN_SCL 1
#define PIN_STATUS 6
#define OUTPUTS 4
#define MAX_PIXELS 600
#define BIT_RATE 800000

static const uint PIXEL_PIN[OUTPUTS] = {2, 3, 4, 5};

enum {
    REG_ID = 0x00, REG_VERSION = 0x02, REG_STATUS = 0x04, REG_FRAME = 0x05, REG_LIMIT = 0x08,
    REG_OUTPUT = 0x10, OUTPUT_STRIDE = 0x10, REG_BOOTSEL = 0x7E, REG_RESTART = 0x7F, REG_COUNT = 0x80,
};
enum { O_MODE, O_R, O_G, O_B, O_BRIGHTNESS, O_SPEED, O_ORDER, O_FLAGS, O_COUNT_L, O_COUNT_H };
enum { MODE_OFF, MODE_SOLID, MODE_RAINBOW };

// The registers as the Pi sees them (written from the I2C interrupt), and the copy the frames are
// drawn from, taken when a write finishes.
static volatile uint8_t regs[REG_COUNT];
static uint8_t live[REG_COUNT];
static volatile bool written;

static struct {
    uint8_t address;
    bool have_address;
    bool wrote;
} xfer;

static bool writable(uint8_t reg) {
    return reg >= REG_LIMIT;
}

static void i2c_handler(i2c_inst_t *i2c, i2c_slave_event_t event) {
    switch (event) {
    case I2C_SLAVE_RECEIVE:
        if (!xfer.have_address) {
            xfer.address = i2c_read_byte_raw(i2c) % REG_COUNT;
            xfer.have_address = true;
        } else {
            uint8_t value = i2c_read_byte_raw(i2c);
            if (writable(xfer.address)) {
                regs[xfer.address] = value;
                xfer.wrote = true;
            }
            xfer.address = (xfer.address + 1) % REG_COUNT;
        }
        break;
    case I2C_SLAVE_REQUEST:
        i2c_write_byte_raw(i2c, regs[xfer.address]);
        xfer.address = (xfer.address + 1) % REG_COUNT;
        break;
    case I2C_SLAVE_FINISH:
        if (xfer.wrote) {
            written = true;
        }
        xfer.have_address = false;
        xfer.wrote = false;
        break;
    default:
        break;
    }
}

static void defaults(void) {
    regs[REG_ID] = 'L';
    regs[REG_ID + 1] = 'B';
    regs[REG_VERSION] = VERSION_MAJOR;
    regs[REG_VERSION + 1] = VERSION_MINOR;
    regs[REG_LIMIT] = 255;
    for (int n = 0; n < OUTPUTS; n++) {
        volatile uint8_t *o = &regs[REG_OUTPUT + OUTPUT_STRIDE * n];
        o[O_MODE] = MODE_OFF;
        o[O_BRIGHTNESS] = 153;
        o[O_SPEED] = 9;
        o[O_ORDER] = 0;
    }
    for (int i = 0; i < REG_COUNT; i++) {
        live[i] = regs[i];
    }
}

// ---------------------------------------------------------------- drawing
static uint32_t frame_buf[OUTPUTS][MAX_PIXELS];

// A fully saturated colour for a hue 0-255, as the app's rainbow (red, yellow, green, cyan,
// blue, magenta, and round to red).
static void hue_to_rgb(uint8_t hue, uint8_t rgb[3]) {
    uint8_t region = hue / 43;
    uint8_t rise = (uint8_t)((hue - region * 43) * 6);
    uint8_t fall = 255 - rise;
    switch (region) {
    case 0: rgb[0] = 255; rgb[1] = rise; rgb[2] = 0; break;
    case 1: rgb[0] = fall; rgb[1] = 255; rgb[2] = 0; break;
    case 2: rgb[0] = 0; rgb[1] = 255; rgb[2] = rise; break;
    case 3: rgb[0] = 0; rgb[1] = fall; rgb[2] = 255; break;
    case 4: rgb[0] = rise; rgb[1] = 0; rgb[2] = 255; break;
    default: rgb[0] = 255; rgb[1] = 0; rgb[2] = fall; break;
    }
}

// Which of R, G, B goes out first, second and third, for each byte order.
static const uint8_t ORDER[6][3] = {{1, 0, 2}, {0, 1, 2}, {2, 0, 1}, {0, 2, 1}, {1, 2, 0}, {2, 1, 0}};

static uint32_t pack(const uint8_t rgb[3], uint32_t scale, uint8_t order) {
    const uint8_t *o = ORDER[order < 6 ? order : 0];
    uint32_t a = rgb[o[0]] * scale / 255, b = rgb[o[1]] * scale / 255, c = rgb[o[2]] * scale / 255;
    return (a << 24) | (b << 16) | (c << 8);    // the PIO shifts out the top 24 bits, MSB first
}

static uint pixel_count(const uint8_t *o) {
    uint count = o[O_COUNT_L] | (o[O_COUNT_H] << 8);
    return count > MAX_PIXELS ? MAX_PIXELS : count;
}

static void draw(int n, uint64_t now_us) {
    const uint8_t *o = &live[REG_OUTPUT + OUTPUT_STRIDE * n];
    uint count = pixel_count(o);
    uint32_t scale = (uint32_t)o[O_BRIGHTNESS] * live[REG_LIMIT] / 255;
    uint8_t rgb[3];
    if (o[O_MODE] == MODE_SOLID) {
        rgb[0] = o[O_R];
        rgb[1] = o[O_G];
        rgb[2] = o[O_B];
        uint32_t word = pack(rgb, scale, o[O_ORDER]);
        for (uint i = 0; i < count; i++) {
            frame_buf[n][i] = word;
        }
    } else if (o[O_MODE] == MODE_RAINBOW) {
        // The whole strip spans one turn of the colour wheel, which moves `speed` turns a minute.
        uint8_t offset = (uint8_t)((now_us / 1000 * o[O_SPEED] * 256) / 60000);
        bool reverse = o[O_FLAGS] & 1;
        for (uint i = 0; i < count; i++) {
            uint k = reverse ? count - 1 - i : i;
            hue_to_rgb((uint8_t)(k * 256 / count + offset), rgb);
            frame_buf[n][i] = pack(rgb, scale, o[O_ORDER]);
        }
    } else {
        memset(frame_buf[n], 0, count * sizeof(uint32_t));
    }
}

// ---------------------------------------------------------------- sending
static PIO pio = pio0;
static int dma[OUTPUTS];

static void outputs_init(void) {
    uint offset = pio_add_program(pio, &ws2812_program);
    float div = (float)clock_get_hz(clk_sys) / (BIT_RATE * (ws2812_T1 + ws2812_T2 + ws2812_T3));
    for (int n = 0; n < OUTPUTS; n++) {
        uint sm = n, pin = PIXEL_PIN[n];
        pio_gpio_init(pio, pin);
        pio_sm_set_consecutive_pindirs(pio, sm, pin, 1, true);
        pio_sm_config c = ws2812_program_get_default_config(offset);
        sm_config_set_sideset_pins(&c, pin);
        sm_config_set_out_shift(&c, false, true, 24);
        sm_config_set_fifo_join(&c, PIO_FIFO_JOIN_TX);
        sm_config_set_clkdiv(&c, div);
        pio_sm_init(pio, sm, offset, &c);
        pio_sm_set_enabled(pio, sm, true);

        dma[n] = dma_claim_unused_channel(true);
        dma_channel_config d = dma_channel_get_default_config(dma[n]);
        channel_config_set_transfer_data_size(&d, DMA_SIZE_32);
        channel_config_set_read_increment(&d, true);
        channel_config_set_write_increment(&d, false);
        channel_config_set_dreq(&d, pio_get_dreq(pio, sm, true));
        dma_channel_configure(dma[n], &d, &pio->txf[sm], frame_buf[n], 0, false);
    }
}

static void send(void) {
    for (int n = 0; n < OUTPUTS; n++) {
        uint count = pixel_count(&live[REG_OUTPUT + OUTPUT_STRIDE * n]);
        if (count) {
            dma_channel_transfer_from_buffer_now(dma[n], frame_buf[n], count);
        }
    }
    for (int n = 0; n < OUTPUTS; n++) {
        dma_channel_wait_for_finish_blocking(dma[n]);
        while (!pio_sm_is_tx_fifo_empty(pio, n)) {
            tight_loop_contents();
        }
    }
    // The last pixel's bits are still going out of the shift register; then the line must stay
    // low for a WS2815's reset (280 us) before the next frame.
    sleep_us(60 + 300);
}

// ---------------------------------------------------------------- the loop
static void commands(void) {
    if (live[REG_BOOTSEL] == 0xB0) {
        reset_usb_boot(0, 0);
    }
    if (live[REG_RESTART] == 0x5A) {
        watchdog_reboot(0, 0, 0);
    }
}

int main(void) {
    gpio_init(PIN_STATUS);
    gpio_set_dir(PIN_STATUS, GPIO_OUT);
    defaults();

    gpio_init(PIN_SDA);
    gpio_init(PIN_SCL);
    gpio_set_function(PIN_SDA, GPIO_FUNC_I2C);
    gpio_set_function(PIN_SCL, GPIO_FUNC_I2C);
    // The bus is pulled up on the board (R41/R42); no internal pull-ups.
    i2c_init(i2c0, 100 * 1000);
    i2c_slave_init(i2c0, I2C_ADDRESS, &i2c_handler);

    outputs_init();
    watchdog_enable(1000, true);

    uint8_t frame = 0;
    regs[REG_STATUS] = 1;
    while (true) {
        watchdog_update();
        if (written) {
            uint32_t irq = save_and_disable_interrupts();
            written = false;
            for (int i = 0; i < REG_COUNT; i++) {
                live[i] = regs[i];
            }
            regs[REG_BOOTSEL] = regs[REG_RESTART] = 0;
            restore_interrupts(irq);
            commands();
        }
        uint64_t now = time_us_64();
        for (int n = 0; n < OUTPUTS; n++) {
            draw(n, now);
        }
        send();
        regs[REG_FRAME] = ++frame;
        gpio_put(PIN_STATUS, (now / 500000) & 1);    // blinks once a second while this loop runs
    }
}
