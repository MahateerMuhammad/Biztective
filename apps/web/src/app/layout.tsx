import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "ROOT", template: "%s · ROOT" },
  description: "Business investigation and recovery platform",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body className="min-h-dvh bg-background text-foreground antialiased">{children}</body>
    </html>
  );
}
