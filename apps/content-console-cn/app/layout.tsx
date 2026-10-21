import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "森穹小悟 · 双屏场景后台",
  description: "森穹小悟 A/B 双屏设备场景配置与管理后台",
  icons: { icon: "/favicon.png" },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
