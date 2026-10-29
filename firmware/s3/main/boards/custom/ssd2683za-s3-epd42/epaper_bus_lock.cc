#include "epaper_bus_lock.h"

SemaphoreHandle_t EpaperBusMutex() {
    static SemaphoreHandle_t mutex = xSemaphoreCreateMutex();
    return mutex;
}
