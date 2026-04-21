import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "AgenticQueue",
  description: "AgenticQueue web shell for Phase 7 observability.",
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
