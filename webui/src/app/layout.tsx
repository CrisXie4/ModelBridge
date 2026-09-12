import type { Metadata } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";

export const metadata: Metadata = {
  title: "ModelBridge 管理后台",
  description: "本地管理 ModelBridge：模型/路由/Skill/提示词/自检",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body>
        {/* Phosphor 图标字体（本地资产，见 public/fonts/phosphor） */}
        <link
          rel="stylesheet"
          href="/fonts/phosphor/phosphor-regular.css"
          precedence="default"
        />
        <link
          rel="stylesheet"
          href="/fonts/phosphor/phosphor-fill.css"
          precedence="default"
        />
        <div className="app-shell">
          <Sidebar />
          <main className="app-main">{children}</main>
        </div>
      </body>
    </html>
  );
}
