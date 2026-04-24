import type { Metadata, Viewport } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "AgenticQueue",
  description: "AgenticQueue web shell for Phase 7 observability.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  colorScheme: "dark",
  themeColor: "#0d1117",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
