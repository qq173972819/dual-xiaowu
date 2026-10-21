"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

const tabs = [["overview","运营概览"],["devices","设备"],["tasks","内容任务"],["templates","内容模板"],["plugins","插件"],["developer-apps","开发者应用"],["webhook-outbox","Webhook 队列"],["audit-logs","审计日志"]];
function value(v) { if (v == null || v === "") return "—"; return typeof v === "object" ? JSON.stringify(v, null, 2) : String(v); }

export default function OperationsConsole() {
  const [tab, setTab] = useState("overview");
  const [key, setKey] = useState("dev-admin-key");
  const [data, setData] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [appName, setAppName] = useState("行业接入应用");
  const [createdToken, setCreatedToken] = useState("");
  async function load(next = tab) {
    setLoading(true); setError("");
    try {
      const res = await fetch(`/backend/admin/v1/${next}`, { headers: { "X-Admin-Key": key }, cache: "no-store" });
      if (!res.ok) throw new Error(res.status === 401 ? "管理员密钥不正确" : `请求失败 HTTP ${res.status}`);
      setData(await res.json()); localStorage.setItem("xiaowu-admin-key", key);
    } catch (e) { setError(e instanceof Error ? e.message : "请求失败"); } finally { setLoading(false); }
  }
  useEffect(() => { const saved = localStorage.getItem("xiaowu-admin-key"); if (saved) setKey(saved); }, []);
  useEffect(() => { void load(tab); }, [tab]); // eslint-disable-line react-hooks/exhaustive-deps
  async function createDeveloperApp() {
    setError(""); setCreatedToken("");
    const res = await fetch("/backend/admin/v1/developer-apps", { method:"POST", headers:{"X-Admin-Key":key,"Content-Type":"application/json"}, body:JSON.stringify({name:appName,scopes:["device.content.write","device.read","device.event.read","webhook.manage"]}) });
    if (!res.ok) return setError(`创建失败 HTTP ${res.status}`);
    const result = await res.json(); setCreatedToken(result.token); await load("developer-apps");
  }
  const overview = !Array.isArray(data) ? data : {}, rows = Array.isArray(data) ? data : [];
  return <main><aside><div className="brand" style={{display:'flex',alignItems:'center',gap:10}}><img src="/logo.png" alt="森穹小悟" width={34} height={34} style={{borderRadius:9,objectFit:'contain',background:'#fff',border:'1px solid #2b4036'}}/><div><span>森穹小悟</span><b>S3 OPERATIONS</b></div></div><nav><Link href="/content-config" className="cc-entry">双屏内容配置 →</Link><Link href="/developer" className="dev-entry">开发者中心 →</Link>{tabs.map(([id,label])=><button className={tab===id?"active":""} key={id} onClick={()=>setTab(id)}>{label}</button>)}</nav><p>运营能力仅保留在 PC 端。小程序只负责用户内容管理。</p></aside><section>
    <header><div><small>OPEN PLATFORM / PC</small><h1>{tabs.find(x=>x[0]===tab)?.[1]}</h1></div><div className="auth"><input value={key} onChange={e=>setKey(e.target.value)} placeholder="管理员密钥"/><button onClick={()=>void load()}>连接后台</button></div></header>
    {error&&<div className="error">{error}</div>}{loading&&<div className="loading">正在读取数据…</div>}
    {tab==="overview"&&!loading&&<div className="metrics"><Metric label="设备总数" value={overview.devices}/><Metric label="已有心跳" value={overview.seen_devices}/><Metric label="待下发任务" value={overview.queued_tasks}/><Metric label="已显示内容" value={overview.displayed_tasks}/></div>}
    {tab==="developer-apps"&&!loading&&<div className="app-create"><input value={appName} onChange={e=>setAppName(e.target.value)} placeholder="应用名称"/><button onClick={()=>void createDeveloperApp()}>创建全权限测试应用</button>{createdToken&&<div><b>请立即保存，仅显示一次：</b><code>{createdToken}</code></div>}</div>}
    {tab!=="overview"&&!loading&&<DataTable rows={rows}/>}</section></main>;
}
function Metric({label,value:v}) { return <article className="metric"><span>{label}</span><b>{value(v)}</b></article>; }
function DataTable({rows}) { if (!rows.length) return <div className="empty">暂无数据</div>; const keys=Object.keys(rows[0]); return <div className="table-wrap"><table><thead><tr>{keys.map(k=><th key={k}>{k}</th>)}</tr></thead><tbody>{rows.map((row,i)=><tr key={i}>{keys.map(k=><td key={k}><pre>{value(row[k])}</pre></td>)}</tr>)}</tbody></table></div>; }
