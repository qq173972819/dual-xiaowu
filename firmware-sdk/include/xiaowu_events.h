#pragma once

typedef enum {
    XIAOWU_EVENT_BUTTON_PRESSED,
    XIAOWU_EVENT_CONTENT_CONFIRMED,
    XIAOWU_EVENT_CONTENT_DISMISSED,
    XIAOWU_EVENT_VOICE_INTENT,
} xiaowu_event_type_t;

// Implemented by the official closed BSP/network adapter.
int xiaowu_report_event(xiaowu_event_type_t type, const char* json_payload);
