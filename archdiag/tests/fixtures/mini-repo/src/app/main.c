#include "can_driver.h"
#include "gpio.h"

int main(void)
{
    can_init(500000);
    gpio_init();
    for (;;) {
        can_frame_t frame;
        if (can_receive(&frame)) {
            gpio_write((unsigned)(frame.id & 0xFu), frame.data[0] != 0);
        }
    }
}
