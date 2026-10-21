import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "森穹小悟 · 双屏内容后台",
  description: "买设备的人使用的双屏内容配置后台",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
