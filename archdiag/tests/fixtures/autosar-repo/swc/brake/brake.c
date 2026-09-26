#include "Rte_BrakeCtrl.h"
#include "brake.h"
#include "../speed/speed.h"

// Brake pressure control. @req SW-REQ-2 @asil D
void BrakeCtrl_Run(void)
{
    int v = 0;
    Rte_Read_RpSpeed_value(&v);
    (void)speed_filter(v);
}
