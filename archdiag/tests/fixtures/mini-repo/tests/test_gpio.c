#include "gpio.h"

int main(void)
{
    gpio_init();
    gpio_write(1, true);
    return 0;
}
