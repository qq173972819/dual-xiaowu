"use client";

import Link from "next/link";
import { useState } from "react";

const endpoints = [
  ["POST", "/openapi/v1/devices/{device_id}/content", "向 A/B 屏下发模板内容", "device.content.write"],
  ["GET", "/openapi/v1/devices/{device_id}", "读取设备能力", "device.read"],
  ["GET", "/openapi/v1/devices/{device_id}/events", "读取按键、确认和语音事件", "device.event.read"],
  ["POST", "/openapi/v1/webhooks", "订阅设备事件与任务结果", "webhook.manage"],
  ["GET", "/openapi/v1/webhooks", "查看当前应用的订阅", "webhook.manage"],
];

const deviceEndpoints = [
  ["POST", "/api/v1/devices/{device_id}/heartbeat", "设备心跳与运行状态"],
  ["GET", "/api/v1/devices/{device_id}/tasks/next", "拉取下一条内容任务"],
  ["POST", "/api/v1/devices/{device_id}/tasks/{task_id}/ack", "确认显示结果"],
  ["POST", "/api/v1/devices/{device_id}/events", "上报按键、确认或语音意图"],
];

export default function DeveloperCenter(){
  const [copied,setCopied]=useState("");
  const base=typeof window!=="undefined"?`${window.location.protocol}//${window.location.hostname}:8100`:"http://127.0.0.1:8100";
  const example=`curl -X POST ${base}/openapi/v1/devices/SIM-S3-001/content \\\n+  -H "Authorization: Bearer YOUR_APP_TOKEN" \\\n+  -H "Content-Type: application/json" \\\n+  -d '{"template":"notice-card","screen":"A","data":{"title":"课程提醒","content":"下午 3 点开始英语课"}}'`;
  async function copy(text,label){await navigator.clipboard.writeText(text);setCopied(label);setTimeout(()=>setCopied(""),1500)}
  return <div className="dev-page"><header className="dev-top"><div><span>OPEN PLATFORM / DEVELOPER</span><h1>开发者中心</h1><p>使用稳定 API、模板和事件，把教育、养老、家庭或办公业务接入森穹小悟 S3。</p></div><Link href="/">返回运营后台</Link></header>
    <section className="dev-hero"><div><small>API BASE URL</small><code>{base}</code></div><div><small>在线 OpenAPI</small><a href={`${base}/docs`} target="_blank">打开 Swagger 文档 ↗</a></div><div><small>机器可读规范</small><a href={`${base}/openapi.json`} target="_blank">openapi.json ↗</a></div></section>
    <div className="dev-grid"><section className="dev-card"><h2>1. 获取应用凭据</h2><p>由平台运营人员在“开发者应用”中创建应用。Token 只显示一次，请保存在服务端，不要放入网页、小程序或设备固件。</p><div className="scope-list">{["device.content.write","device.read","device.event.read","webhook.manage"].map(x=><code key={x}>{x}</code>)}</div><p className="dev-note">请求头：<code>Authorization: Bearer YOUR_APP_TOKEN</code></p></section>
      <section className="dev-card"><h2>2. 推送第一条内容</h2><pre>{example}</pre><button onClick={()=>copy(example,"curl")}>{copied==="curl"?"已复制":"复制示例"}</button></section></div>
    <section className="dev-card"><h2>第三方业务接口</h2><ApiTable rows={endpoints}/></section>
    <section className="dev-card"><h2>设备固件接口</h2><p>这组接口使用每台设备独立的 <code>X-Device-Token</code>，第三方业务系统不应持有设备永久令牌。</p><ApiTable rows={deviceEndpoints}/></section>
    <div className="dev-grid"><section className="dev-card"><h2>内容模板</h2><ul><li><code>notice-card</code> 通用通知</li><li><code>homework-list</code> 今日作业</li><li><code>vocabulary-card</code> 单词学习</li><li><code>care-reminder</code> 照护提醒</li><li><code>daily-schedule</code> 今日安排</li></ul><p>服务端会校验模板必填字段，字段不完整返回 HTTP 422。</p></section>
      <section className="dev-card"><h2>SDK 与示例</h2><ul><li><code>sdk/python/xiaowu_client.py</code></li><li><code>sdk/typescript/xiaowu-client.ts</code></li><li><code>tools/s3_simulator.py</code></li><li><code>docs/01-quickstart/</code></li></ul><p>模拟器可以在没有实体硬件时完成推送、取任务和确认闭环。</p></section></div>
    <section className="dev-card"><h2>Webhook</h2><p>订阅地址必须是 HTTPS。设备按键、内容确认及语音意图进入可靠 Outbox，可在运营后台的“Webhook 队列”查看。正式生产投递将使用签名密钥验证来源。</p></section>
  </div>;
}

function ApiTable({rows}){return <div className="api-table"><table><thead><tr><th>方法</th><th>路径</th><th>说明</th><th>权限</th></tr></thead><tbody>{rows.map((r,i)=><tr key={i}><td><b className={`method ${r[0].toLowerCase()}`}>{r[0]}</b></td><td><code>{r[1]}</code></td><td>{r[2]}</td><td>{r[3]?<code>{r[3]}</code>:"设备令牌"}</td></tr>)}</tbody></table></div>}
