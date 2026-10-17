import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { headers } from "next/headers";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Biztective", template: "%s · Biztective" },
  description: "Business investigation and recovery platform",
};

// One typeface for everything: Inter, variable, with tabular numerals switched on where numbers line up.
const sans = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });

/** Runs before first paint, so the page never flashes the wrong theme. The first visit follows the OS setting;
 * after that the choice stored by the toggle (`biztective-theme`) wins; the old `root-theme` key is read once as a fallback. */
const THEME_SCRIPT =
  "try{var t=localStorage.getItem('biztective-theme');if(t!=='light'&&t!=='dark'){t=localStorage.getItem('root-theme');if(t==='light'||t==='dark')localStorage.setItem('biztective-theme',t)}if(t!=='light'&&t!=='dark'){t=matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'}document.documentElement.classList.toggle('dark',t==='dark')}catch(e){}";

export default async function RootLayout({ children }: LayoutProps<"/">) {
  // The CSP only allows scripts that carry the per-request nonce (src/proxy.ts).
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  return (
    <html lang="en" className={sans.variable} suppressHydrationWarning>
      <head>
        <script nonce={nonce}>{THEME_SCRIPT}</script>
      </head>
      <body className="min-h-dvh bg-background text-foreground antialiased">{children}</body>
    </html>
  );
}
