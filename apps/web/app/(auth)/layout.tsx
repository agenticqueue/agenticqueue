import type { ReactNode } from "react";
import { Inter, JetBrains_Mono } from "next/font/google";

import styles from "./auth-layout.module.css";

const inter = Inter({
  display: "swap",
  subsets: ["latin"],
  variable: "--font-inter",
});

const jetBrainsMono = JetBrains_Mono({
  display: "swap",
  subsets: ["latin"],
  variable: "--font-jetbrains-mono",
});

export default function AuthLayout({
  children,
}: Readonly<{
  children: ReactNode;
}>) {
  return (
    <section
      className={`${inter.variable} ${jetBrainsMono.variable} ${styles.authShell}`}
    >
      {children}
    </section>
  );
}
