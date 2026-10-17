import type { Metadata } from "next";
import { Geist, JetBrains_Mono } from "next/font/google";
import { headers } from "next/headers";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "ROOT", template: "%s · ROOT" },
  description: "Business investigation and recovery platform",
};

// Sans for headings, prose and navigation; mono only for numbers, units, small caps titles and tables.
const sans = Geist({ subsets: ["latin"], variable: "--font-geist-sans", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-jetbrains-mono", display: "swap" });

/** Runs before first paint, so the page never flashes the wrong theme. The first visit follows the OS setting;
 * after that the choice stored by the toggle (`root-theme`) wins. */
const THEME_SCRIPT =
  "try{var t=localStorage.getItem('root-theme');if(t!=='light'&&t!=='dark'){t=matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'}document.documentElement.classList.toggle('dark',t==='dark')}catch(e){}";

export default async function RootLayout({ children }: LayoutProps<"/">) {
  // The CSP only allows scripts that carry the per-request nonce (src/proxy.ts).
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`} suppressHydrationWarning>
      <head>
        <script nonce={nonce}>{THEME_SCRIPT}</script>
      </head>
      <body className="min-h-dvh bg-background text-foreground antialiased">{children}</body>
    </html>
  );
}
