// 后端统一为 xiaowu_platform（8104）。旧兼容后端 services/legacy-api 已让出 8104，
// 默认地址同步更新；如需指向别处，用 XIAOWU_API_BASE 覆盖。
const backend = (process.env.XIAOWU_API_BASE || "http://127.0.0.1:8104").replace(/\/$/, "");
const config = { async rewrites() { return [{ source: "/backend/:path*", destination: `${backend}/:path*` }]; } };
export default config;
