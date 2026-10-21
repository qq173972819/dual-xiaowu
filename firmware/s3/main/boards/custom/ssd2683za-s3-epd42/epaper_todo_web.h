#pragma once

#include <esp_http_server.h>
#include <functional>

// Tiny LAN-only editor for the e-paper todo list and weekly schedule. It
// stores the same JSON consumed by EpaperDashboard and deliberately has no
// dependency on LCD UI. The changed callback receives "todo" or "schedule"
// so the board can refresh the matching page.
class EpaperTodoWeb {
public:
    explicit EpaperTodoWeb(std::function<void(const char* kind)> changed);
    ~EpaperTodoWeb();
    bool Start();
    bool IsRunning() const { return server_ != nullptr; }

private:
    static esp_err_t TodoPage(httpd_req_t* req);
    static esp_err_t SchedulePage(httpd_req_t* req);
    static esp_err_t GetTodos(httpd_req_t* req);
    static esp_err_t PutTodos(httpd_req_t* req);
    static esp_err_t GetSchedule(httpd_req_t* req);
    static esp_err_t PutSchedule(httpd_req_t* req);
    httpd_handle_t server_ = nullptr;
    std::function<void(const char* kind)> changed_;
};
