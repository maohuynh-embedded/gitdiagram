#ifndef CAN_DRIVER_H
#define CAN_DRIVER_H

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    uint32_t id;
    uint8_t len;
    uint8_t data[8];
} can_frame_t;

void can_init(uint32_t bitrate);
bool can_receive(can_frame_t *frame);
bool can_send(const can_frame_t *frame);

#endif
