"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

const tabs = [["overview","运营概览"],["creator-accounts","注册用户"],["developer-submissions","贡献应用"],["devices","设备"],["tasks","内容任务"],["templates","内容模板"],["plugins","插件"],["developer-apps","API 应用"],["webhook-outbox","Webhook 队列"],["audit-logs","审计日志"]];
function value(v) { if (v == null || v === "") return "—"; return typeof v === "object" ? JSON.stringify(v, null, 2) : String(v); }

export default function OperationsConsole() {
  const [tab, setTab] = useState("overview");
  const [token, setToken] = useState("");
  const [data, setData] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [appName, setAppName] = useState("行业接入应用");
  const [createdToken, setCreatedToken] = useState("");
  const authHeaders = token ? { Authorization: `Bearer ${token}` } : {};
  async function load(next = tab) {
    setLoading(true); setError("");
    try {
      const res = await fetch(`/backend/admin/v1/${next}`, { headers: authHeaders, cache: "no-store" });
      if (!res.ok) throw new Error(res.status === 401 ? "登录已过期，请重新登录" : `请求失败 HTTP ${res.status}`);
      setData(await res.json());
    } catch (e) { setError(e instanceof Error ? e.message : "请求失败"); } finally { setLoading(false); }
  }
  useEffect(() => { setToken(localStorage.getItem("xiaowu-admin-token")||""); }, []);
  useEffect(() => { if(token) void load(tab); }, [tab,token]); // eslint-disable-line react-hooks/exhaustive-deps
  async function login(e){e.preventDefault();setError("");const body=Object.fromEntries(new FormData(e.currentTarget));const res=await fetch("/backend/admin/v1/auth/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});const result=await res.json().catch(()=>({}));if(!res.ok)return setError(result.detail||"登录失败");localStorage.setItem("xiaowu-admin-token",result.token);setToken(result.token)}
  function logout(){localStorage.removeItem("xiaowu-admin-token");setToken("");setData([])}
  async function createDeveloperApp() {
    setError(""); setCreatedToken("");
    const res = await fetch("/backend/admin/v1/developer-apps", { method:"POST", headers:{...authHeaders,"Content-Type":"application/json"}, body:JSON.stringify({name:appName,scopes:["device.content.write","device.read","device.event.read","webhook.manage"]}) });
    if (!res.ok) return setError(`创建失败 HTTP ${res.status}`);
    const result = await res.json(); setCreatedToken(result.token); await load("developer-apps");
  }
  if(!token)return <main className="admin-login-page"><section className="admin-login-card"><img src="/logo.png" alt="森穹小悟"/><small>PLATFORM ADMINISTRATION</small><h1>小悟众创管理后台</h1><p>查看注册用户、贡献应用与审核状态。</p><form onSubmit={login}><label>管理员账号<input name="username" autoComplete="username" required/></label><label>密码<input name="password" type="password" autoComplete="current-password" required/></label>{error&&<div className="error">{error}</div>}<button>登录管理后台</button></form></section></main>;
  const overview = !Array.isArray(data) ? data : {}, rows = Array.isArray(data) ? data : [];
  return <main className="admin-app"><aside><div className="brand" style={{display:'flex',alignItems:'center',gap:10}}><img src="/logo.png" alt="森穹小悟" width={34} height={34} style={{borderRadius:9,objectFit:'contain',background:'#fff',border:'1px solid #2b4036'}}/><div><span>森穹小悟</span><b>S3 OPERATIONS</b></div></div><nav><Link href="/content-config" className="cc-entry">双屏内容配置 →</Link><Link href="/developer" className="dev-entry">开发者中心 →</Link>{tabs.map(([id,label])=><button className={tab===id?"active":""} key={id} onClick={()=>setTab(id)}>{label}</button>)}</nav><p>运营能力仅保留在 PC 端。小程序只负责用户内容管理。</p></aside><section>
    <header><div><small>OPEN PLATFORM / ADMIN</small><h1>{tabs.find(x=>x[0]===tab)?.[1]}</h1></div><div className="auth"><button onClick={()=>void load()}>刷新</button><button className="logout-admin" onClick={logout}>退出登录</button></div></header>
    {error&&<div className="error">{error}</div>}{loading&&<div className="loading">正在读取数据…</div>}
    {tab==="overview"&&!loading&&<div className="metrics"><Metric label="设备总数" value={overview.devices}/><Metric label="已有心跳" value={overview.seen_devices}/><Metric label="待下发任务" value={overview.queued_tasks}/><Metric label="已显示内容" value={overview.displayed_tasks}/></div>}
    {tab==="developer-apps"&&!loading&&<div className="app-create"><input value={appName} onChange={e=>setAppName(e.target.value)} placeholder="应用名称"/><button onClick={()=>void createDeveloperApp()}>创建全权限测试应用</button>{createdToken&&<div><b>请立即保存，仅显示一次：</b><code>{createdToken}</code></div>}</div>}
    {tab==="developer-submissions"&&!loading&&<ReviewTable rows={rows} token={token} reload={()=>load("developer-submissions")} setError={setError}/>}
    {tab!=="overview"&&tab!=="developer-submissions"&&!loading&&<DataTable rows={rows}/>}</section></main>;
}
function Metric({label,value:v}) { return <article className="metric"><span>{label}</span><b>{value(v)}</b></article>; }
function DataTable({rows}) { if (!rows.length) return <div className="empty">暂无数据</div>; const keys=Object.keys(rows[0]); return <div className="table-wrap"><table><thead><tr>{keys.map(k=><th key={k}>{k}</th>)}</tr></thead><tbody>{rows.map((row,i)=><tr key={i}>{keys.map(k=><td key={k}><pre>{value(row[k])}</pre></td>)}</tr>)}</tbody></table></div>; }
function ReviewTable({rows,token,reload,setError}) { async function decide(id,decision){const note=window.prompt(decision==="approve"?"发布备注（可留空）":"驳回原因")||"";const res=await fetch(`/backend/admin/v1/developer-submissions/${id}/decision`,{method:"POST",headers:{Authorization:`Bearer ${token}`,"Content-Type":"application/json"},body:JSON.stringify({decision,note})});if(!res.ok){setError(`处理失败 HTTP ${res.status}`);return}reload()} if(!rows.length)return <div className="empty">暂无开发者提交</div>;return <div className="review-list">{rows.map(a=><article key={a.app_id}><div><span className={`status ${a.status}`}>{a.status}</span><h3>{a.name}</h3><p>{a.description||"暂无说明"}</p><code>{a.app_id} · {a.scene} · v{a.version}</code></div><div>{a.status==="in_review"?<><button onClick={()=>decide(a.app_id,"reject")}>驳回</button><button className="approve" onClick={()=>decide(a.app_id,"approve")}>审核并上线</button></>:<small>{a.review_note||"—"}</small>}</div></article>)}</div> }
