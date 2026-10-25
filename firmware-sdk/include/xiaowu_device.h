#pragma once
#include <stdint.h>

// Public logical protocol types. No PCB pins, waveforms or BSP internals.
typedef enum {
    XIAOWU_SCREEN_A = 0,
    XIAOWU_SCREEN_B = 1,
    XIAOWU_SCREEN_BOTH = 2,
} XIAOWU_screen_t;

typedef struct {
    const char* device_id;
    const char* model;
    const char* firmware_version;
    uint8_t screen_count;
    uint8_t has_audio_input;
    uint8_t has_audio_output;
    uint8_t has_storage;
} XIAOWU_device_capabilities_t;
