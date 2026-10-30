#include "can_driver.h"

static uint32_t s_bitrate;

void can_init(uint32_t bitrate) { s_bitrate = bitrate; }

bool can_receive(can_frame_t *frame)
{
    (void)frame;
    return false;
}

bool can_send(const can_frame_t *frame)
{
    (void)frame;
    return s_bitrate != 0;
}
