#include "Rte_SpeedSensor.h"
#include "speed.h"

/* Wheel speed acquisition.
 * @req SW-REQ-1
 * @asil B
 */
int speed_filter(int raw) { return raw / 2; }

void SpeedSensor_Run(void)
{
    Rte_Write_PpSpeed_value(speed_filter(42));
    Rte_Write_PpGhost_value(1);
}
