# The LED board's RP2040 firmware. Builds with Raspberry Pi's Pico SDK (2.x):
#   export PICO_SDK_PATH=/path/to/pico-sdk
#   cmake -B build && cmake --build build      ->  build/led_board.uf2
# The board is a Pico as far as the SDK is concerned: a 12 MHz crystal and 2 MB of W25Q flash.
cmake_minimum_required(VERSION 3.13)

set(PICO_BOARD pico CACHE STRING "Board type")
include(pico_sdk_import.cmake)

project(led_board C CXX ASM)
set(CMAKE_C_STANDARD 11)
pico_sdk_init()

add_executable(led_board main.c)
pico_generate_pio_header(led_board ${CMAKE_CURRENT_LIST_DIR}/ws2812.pio)
target_compile_options(led_board PRIVATE -Wall -Wextra -Werror)
target_link_libraries(led_board pico_stdlib pico_i2c_slave hardware_pio hardware_dma hardware_i2c hardware_watchdog)
pico_enable_stdio_usb(led_board 0)
pico_enable_stdio_uart(led_board 0)
pico_add_extra_outputs(led_board)
