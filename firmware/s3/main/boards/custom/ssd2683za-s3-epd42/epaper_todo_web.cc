#include "epaper_todo_web.h"

#include <cJSON.h>
#include <esp_log.h>
#include <cstdio>
#include <ctime>
#include <string>
#include <utility>

#include "settings.h"

namespace {
constexpr char kTag[] = "EpaperTodoWeb";

constexpr char kTodoPage[] = R"HTML(<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>墨水屏待办</title>
<style>body{font:16px sans-serif;max-width:720px;margin:30px auto;padding:0 16px}li{margin:10px 0}.done{text-decoration:line-through;color:#777}button,input,select{font:inherit;padding:7px;margin:3px}a{margin-right:12px}</style>
<h1>墨水屏</h1><p><a href="/todo">待办</a><a href="/schedule">课程表</a></p>
<h2>待办</h2><form id=f><input id=t required placeholder="待办内容"><select id=p><option value=1>高</option><option value=2 selected>中</option><option value=3>低</option></select><button>添加</button></form><ul id=l></ul>
<script>let a=[];async function load(){a=await(await fetch('/api/todos')).json();draw()}function draw(){l.innerHTML='';a.forEach((x,i)=>{let e=document.createElement('li');e.className=x.completed?'done':'';e.innerHTML=`<input type=checkbox ${x.completed?'checked':''}> [${['','高','中','低'][x.priority]||'中'}] ${x.text} <button>删除</button>`;e.children[0].onchange=()=>{x.completed=!x.completed;save()};e.children[1].onclick=()=>{a.splice(i,1);save()};l.append(e)})}async function save(){await fetch('/api/todos',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(a)});draw()}f.onsubmit=e=>{e.preventDefault();a.push({text:t.value,priority:+p.value,completed:false});t.value='';save()};load()</script></html>)HTML";

// 课程表编辑页：5 天 × 10 节网格。节次时间固定（与墨水屏固件一致）。
// 首次打开（设备未保存过课表）时用内置默认课表回填，家长微调后保存即可。
constexpr char kSchedulePage[] = R"HTML(<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>墨水屏课程表</title>
<style>body{font:16px sans-serif;max-width:980px;margin:20px auto;padding:0 10px}a{margin-right:12px}table{border-collapse:collapse;width:100%;font-size:13px}th,td{border:1px solid #999;padding:3px;text-align:center}th{background:#eee}td .n{width:52%}td .t{width:40%}td .p{color:#888;font-size:11px;white-space:nowrap}button{font:inherit;padding:8px 18px;margin:8px 4px}</style>
<h1>墨水屏</h1><p><a href="/todo">待办</a><a href="/schedule">课程表</a></p>
<h2>课程表</h2>
<p>开学日期 <input type=date id=sem value="2026-09-01">（用于计算第几周）</p>
<table id=tbl><thead><tr><th>节次</th><th>周一</th><th>周二</th><th>周三</th><th>周四</th><th>周五</th></tr></thead><tbody></tbody></table>
<p><button onclick=save()>保存到设备</button><span id=msg></span></p>
<script>
const P=[["08:00","08:40"],["08:50","09:30"],["09:45","10:25"],["11:00","11:35"],["11:50","12:25"],["14:10","14:50"],["15:05","15:45"],["16:00","16:40"],["16:55","17:35"],["17:50","18:30"]];
const DAYS=["周一","周二","周三","周四","周五"];
// 内置默认课表（成都七中初三16班 2026-2027 上期）：[科目, 老师]
const DEF=[
["升旗仪式",""],["数学","戴蔚华"],["英语","胡敏"],["语文","李文巧"],["体育","熊伟"],["化学","刘佳璐"],["物理","何薇帆"],["班会/生态安全","何薇帆"],["英自","胡敏"],["语自","李文巧"],
["物理","何薇帆"],["语文","李文巧"],["体育","熊伟"],["数学","戴蔚华"],["数学","戴蔚华"],["化自","刘佳璐"],["英语","胡敏"],["自习",""],["自习",""],["历史","吴倩"],
["数学","戴蔚华"],["语文","李文巧"],["英语","胡敏"],["化学","刘佳璐"],["历史","吴倩"],["语自","李文巧"],["体育","熊伟"],["物自","何薇帆"],["数自","戴蔚华"],["道德与法治","肖芳"],
["英语","胡敏"],["英语","胡敏"],["物理","何薇帆"],["数学","戴蔚华"],["化学","刘佳璐"],["语文","李文巧"],["道德与法治","肖芳"],["体育","熊伟"],["年级测试",""],["年级测试",""],
["语文","李文巧"],["语文","李文巧"],["英语","胡敏"],["物理","何薇帆"],["数学","戴蔚华"],["体育","熊伟"],["英自","胡敏"],["化学","刘佳璐"],["物自","何薇帆"],["数自","戴蔚华"]];
const tb=document.querySelector('#tbl tbody');
for(let p=0;p<10;p++){
 let r=document.createElement('tr');
 r.innerHTML=`<td class=p>第${p+1}节<br>${P[p][0]}</td>`;
 for(let d=0;d<5;d++){
   let cell=document.createElement('td');
   let idx=d*10+p;
   cell.innerHTML=`<input class=n id="n${idx}" placeholder="科目"><br><input class=t id="t${idx}" placeholder="老师">`;
   r.append(cell);
 }
 tb.append(r);
}
function fill(courses){
 for(let p=0;p<10;p++)for(let d=0;d<5;d++){let idx=d*10+p;document.getElementById('n'+idx).value='';document.getElementById('t'+idx).value='';}
 (courses||[]).forEach(c=>{
   let d=c.day-1; if(d<0||d>4)return;
   let p=P.findIndex(x=>x[0]===c.start); if(p<0)return;
   let idx=d*10+p;
   document.getElementById('n'+idx).value=c.name||'';
   document.getElementById('t'+idx).value=c.room||'';
 });
}
async function load(){
 try{
   let d=await(await fetch('/api/schedule')).json();
   if(d&&d.courses&&d.courses.length){fill(d.courses);if(d.semester_date)document.getElementById('sem').value=d.semester_date;}
   else fill(DEF.map(x=>({name:x[0],room:x[1]})));
 }catch(e){fill(DEF.map(x=>({name:x[0],room:x[1]})));}
}
async function save(){
 let courses=[];
 for(let d=0;d<5;d++)for(let p=0;p<10;p++){
   let idx=d*10+p;
   let name=document.getElementById('n'+idx).value.trim();
   if(!name)continue;
   courses.push({day:d+1,start:P[p][0],end:P[p][1],name:name,room:document.getElementById('t'+idx).value.trim()});
 }
 let body={semester_date:document.getElementById('sem').value,courses:courses};
 let r=await fetch('/api/schedule',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 document.getElementById('msg').textContent=r.ok?'已保存，墨水屏课程表已更新':'保存失败';
}
load();
</script></html>)HTML";

std::string ReadBody(httpd_req_t* req) {
    if (req->content_len <= 0 || req->content_len > 16384) return "";
    std::string body(req->content_len, '\0');
    int received = 0;
    while (received < req->content_len) {
        const int n = httpd_req_recv(req, body.data() + received,
                                     req->content_len - received);
        if (n <= 0) return "";
        received += n;
    }
    return body;
}
}  // namespace

EpaperTodoWeb::EpaperTodoWeb(std::function<void(const char* kind)> changed)
    : changed_(std::move(changed)) {}
EpaperTodoWeb::~EpaperTodoWeb() { Stop(); }

void EpaperTodoWeb::Stop() {
    if (!server_) return;
    httpd_handle_t server = server_;
    server_ = nullptr;  // 先置空，避免并发/重入时重复 stop 同一个句柄
    httpd_stop(server);
}

bool EpaperTodoWeb::Start() {
    if (server_) return true;
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    // Keep port 80 free for Xiaozhi's Wi-Fi provisioning/captive portal.
    config.server_port = 8080;
    // 控制套接字端口必须全局唯一：esp-wifi-connect 的配网门户也用
    // HTTPD_DEFAULT_CONFIG()（ctrl_port = 32768）。两个 httpd 实例抢同一个
    // ctrl socket 会让配网门户的 httpd_start() 失败，进而被组件里的
    // ESP_ERROR_CHECK 打挂 → 长按按键直接进入配网时整机重启。
    config.ctrl_port = 32769;
    config.stack_size = 8192;
    config.max_uri_handlers = 8;
    // 局域网内并发极少，压低会话数（默认 7），给配网门户 / MQTT / TLS 留配额。
    config.max_open_sockets = 3;
    if (httpd_start(&server_, &config) != ESP_OK) {
        server_ = nullptr;
        return false;
    }
    const httpd_uri_t routes[] = {
        {.uri="/todo", .method=HTTP_GET, .handler=TodoPage, .user_ctx=this},
        {.uri="/schedule", .method=HTTP_GET, .handler=SchedulePage, .user_ctx=this},
        {.uri="/api/todos", .method=HTTP_GET, .handler=GetTodos, .user_ctx=this},
        {.uri="/api/todos", .method=HTTP_POST, .handler=PutTodos, .user_ctx=this},
        {.uri="/api/schedule", .method=HTTP_GET, .handler=GetSchedule, .user_ctx=this},
        {.uri="/api/schedule", .method=HTTP_POST, .handler=PutSchedule, .user_ctx=this},
    };
    for (const auto& route : routes) httpd_register_uri_handler(server_, &route);
    ESP_LOGI(kTag, "Editor ready: http://<device-ip>:8080/todo and /schedule");
    return true;
}

esp_err_t EpaperTodoWeb::TodoPage(httpd_req_t* req) {
    httpd_resp_set_type(req, "text/html; charset=utf-8");
    return httpd_resp_send(req, kTodoPage, HTTPD_RESP_USE_STRLEN);
}

esp_err_t EpaperTodoWeb::SchedulePage(httpd_req_t* req) {
    httpd_resp_set_type(req, "text/html; charset=utf-8");
    return httpd_resp_send(req, kSchedulePage, HTTPD_RESP_USE_STRLEN);
}

esp_err_t EpaperTodoWeb::GetTodos(httpd_req_t* req) {
    Settings settings("epd_dash", false);
    const std::string json = settings.GetString("todos_json", "[]");
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_send(req, json.c_str(), json.size());
}

esp_err_t EpaperTodoWeb::PutTodos(httpd_req_t* req) {
    const std::string body = ReadBody(req);
    if (body.empty()) {
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "invalid size");
    }
    cJSON* json = cJSON_ParseWithLength(body.data(), body.size());
    if (!cJSON_IsArray(json)) {
        if (json) cJSON_Delete(json);
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "expected JSON array");
    }
    cJSON_Delete(json);
    Settings settings("epd_dash", true);
    settings.SetString("todos_json", body);
    auto* self = static_cast<EpaperTodoWeb*>(req->user_ctx);
    if (self->changed_) self->changed_("todo");
    return httpd_resp_sendstr(req, "ok");
}

esp_err_t EpaperTodoWeb::GetSchedule(httpd_req_t* req) {
    Settings settings("epd_dash", false);
    // 优先网页自己保存的副本（含 semester_date 原样格式）；没有则回退到
    // 固件渲染所用的 schedule 键（可能是语音/MCP 写入的固件 schema）。
    std::string json = settings.GetString("schedule_json", "");
    if (json.empty() || json == "{}") {
        json = settings.GetString("schedule", "{}");
    }
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_send(req, json.c_str(), json.size());
}

esp_err_t EpaperTodoWeb::PutSchedule(httpd_req_t* req) {
    const std::string body = ReadBody(req);
    cJSON* root = cJSON_ParseWithLength(body.data(), body.size());
    if (!cJSON_IsObject(root)) {
        if (root) cJSON_Delete(root);
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "expected JSON object");
    }
    cJSON* courses = cJSON_GetObjectItem(root, "courses");
    if (!cJSON_IsArray(courses)) {
        cJSON_Delete(root);
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "courses must be an array");
    }
    // 网页传的是开学日期 "YYYY-MM-DD"（本地时间），转成 unix 秒供固件算周数。
    std::string semester_date;
    cJSON* date = cJSON_GetObjectItem(root, "semester_date");
    if (cJSON_IsString(date) && date->valuestring) semester_date = date->valuestring;
    int64_t semester_start = 0;
    int y = 0, m = 0, d = 0;
    if (std::sscanf(semester_date.c_str(), "%d-%d-%d", &y, &m, &d) == 3 &&
        y >= 2020 && m >= 1 && m <= 12 && d >= 1 && d <= 31) {
        std::tm t = {};
        t.tm_year = y - 1900;
        t.tm_mon = m - 1;
        t.tm_mday = d;
        t.tm_hour = 0;
        // 本地时区零点转 unix：mktime 按本地时区解释。
        semester_start = static_cast<int64_t>(mktime(&t));
    }
    // 重写成固件消费的 schema：{semester_start, courses:[{day,start,end,name,room}]}
    cJSON* out = cJSON_CreateObject();
    cJSON_AddNumberToObject(out, "semester_start", static_cast<double>(semester_start));
    cJSON* out_courses = cJSON_AddArrayToObject(out, "courses");
    cJSON* item = nullptr;
    cJSON_ArrayForEach(item, courses) {
        cJSON* name = cJSON_GetObjectItem(item, "name");
        cJSON* day = cJSON_GetObjectItem(item, "day");
        if (!cJSON_IsString(name) || !cJSON_IsNumber(day)) continue;
        cJSON* c = cJSON_CreateObject();
        cJSON_AddNumberToObject(c, "day", day->valueint);
        cJSON* start = cJSON_GetObjectItem(item, "start");
        cJSON* end = cJSON_GetObjectItem(item, "end");
        cJSON* room = cJSON_GetObjectItem(item, "room");
        cJSON_AddStringToObject(c, "start", cJSON_IsString(start) ? start->valuestring : "");
        cJSON_AddStringToObject(c, "end", cJSON_IsString(end) ? end->valuestring : "");
        cJSON_AddStringToObject(c, "name", name->valuestring);
        cJSON_AddStringToObject(c, "room", cJSON_IsString(room) ? room->valuestring : "");
        cJSON_AddItemToArray(out_courses, c);
    }
    char* raw = cJSON_PrintUnformatted(out);
    std::string stored = raw ? raw : "{}";
    if (raw) cJSON_free(raw);
    cJSON_Delete(out);
    cJSON_Delete(root);
    Settings settings("epd_dash", true);
    settings.SetString("schedule_json", body);  // 网页回显用（保留日期格式）
    settings.SetString("schedule", stored);     // 固件渲染用
    auto* self = static_cast<EpaperTodoWeb*>(req->user_ctx);
    if (self->changed_) self->changed_("schedule");
    return httpd_resp_sendstr(req, "ok");
}
