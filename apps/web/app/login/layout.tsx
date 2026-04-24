import type { ReactNode } from "react";
import { Inter, JetBrains_Mono } from "next/font/google";

import styles from "../(auth)/auth-layout.module.css";

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

export default function LoginLayout({
  children,
}: Readonly<{
  children: ReactNode;
}>) {
  return (
    <section
      className={`${inter.variable} ${jetBrainsMono.variable} ${styles.authShell}`}
      data-auth-route="login"
    >
      {children}
    </section>
  );
}
