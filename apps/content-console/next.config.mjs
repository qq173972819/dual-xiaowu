const backend = (process.env.XIAOWU_API_BASE || "http://127.0.0.1:8100").replace(/\/$/, "");
const config = { async rewrites() { return [{ source: "/backend/:path*", destination: `${backend}/:path*` }]; } };
export default config;
