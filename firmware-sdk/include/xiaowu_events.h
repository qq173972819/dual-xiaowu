#pragma once

typedef enum {
    XIAOWU_EVENT_BUTTON_PRESSED,
    XIAOWU_EVENT_CONTENT_CONFIRMED,
    XIAOWU_EVENT_CONTENT_DISMISSED,
    XIAOWU_EVENT_VOICE_INTENT,
} XIAOWU_event_type_t;

// Implemented by the official closed BSP/network adapter.
int XIAOWU_report_event(XIAOWU_event_type_t type, const char* json_payload);
