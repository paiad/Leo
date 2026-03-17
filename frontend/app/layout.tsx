import type { Metadata } from "next";
import { Plus_Jakarta_Sans } from "next/font/google";
import { WorkspaceLayoutShell } from "@/shared/components/layout/workspace-layout-shell";
import "./globals.css";

const plusJakartaSans = Plus_Jakarta_Sans({
  subsets: ["latin"],
  variable: "--font-sans",
});

export const metadata: Metadata = {
  title: "OpenLeo",
  description: "OpenLeo AI Agent 工作台前端",
  icons: {
    icon: "/Leo.png",
    shortcut: "/Leo.png",
    apple: "/Leo.png",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className={plusJakartaSans.variable}>
        <WorkspaceLayoutShell>{children}</WorkspaceLayoutShell>
      </body>
    </html>
  );
}
