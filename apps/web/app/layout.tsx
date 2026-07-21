import type { Metadata } from "next";

import { AppShell } from "@/components/app-shell";

import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "Quant Research Lab",
  description: "Local-first, agent-assisted strategy research workspace",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
