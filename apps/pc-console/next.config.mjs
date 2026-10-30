// 后端统一为 xiaowu_platform（8104）。旧兼容后端 services/legacy-api 已让出 8104，
// 默认地址同步更新；如需指向别处，用 XIAOWU_API_BASE 覆盖。
const backend = (process.env.XIAOWU_API_BASE || "http://127.0.0.1:8104").replace(/\/$/, "");

// HTML 文档必须每次回源校验：Next 的 chunk 文件名每次构建都会变，旧文件会被删除，
// 一旦 HTML 被 CDN / Nginx 长缓存，旧 HTML 就会引用 404 的 CSS，表现为「布局全乱」。
const noStore = [{ key: "Cache-Control", value: "no-store, must-revalidate" }];

const config = {
  async rewrites() {
    return [{ source: "/backend/:path*", destination: `${backend}/:path*` }];
  },

  async headers() {
    return [
      { source: "/", headers: noStore },
      { source: "/developer", headers: noStore },
      { source: "/content-config", headers: noStore },
    ];
  },
};

export default config;
