#ifndef GPIO_H
#define GPIO_H

#include <stdbool.h>

void gpio_init(void);
void gpio_write(unsigned pin, bool level);

#endif
