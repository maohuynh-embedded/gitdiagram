#include "gpio.h"

static unsigned s_state;

void gpio_init(void) { s_state = 0; }

void gpio_write(unsigned pin, bool level)
{
    if (level) {
        s_state |= 1u << pin;
    } else {
        s_state &= ~(1u << pin);
    }
}
