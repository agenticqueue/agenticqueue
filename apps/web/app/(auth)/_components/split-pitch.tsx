import type { ReactNode } from "react";

import styles from "./split-pitch.module.css";

export type SplitPitchVariant = "setup" | "login" | "done";

type SplitPitchProps = Readonly<{
  children?: ReactNode;
  variant?: SplitPitchVariant;
}>;

export function SplitPitch({ children, variant = "setup" }: SplitPitchProps) {
  return (
    <div className={`${styles.split} split`} data-auth-variant={variant}>
      <aside className={`${styles.splitLeft} split-left`}>
        <div className={`${styles.pitchBrand} pitch-brand`}>
          <span className={`${styles.brandMark} brand-mark`}>AQ</span>
          <span className={`${styles.brandName} brand-name`}>AgenticQueue</span>
        </div>
        <div className={`${styles.pitch} pitch`}>
          <h2>A read-only queue for the agents in your org.</h2>
          <p>
            Watch pipelines, work, decisions and learnings in real time.
            Self-hosted. One binary. Bring your own model.
          </p>
          <ul className={`${styles.pitchFeats} pitch-feats`}>
            <li>
              <span className={`${styles.dot} dot`}>◆</span>
              Cookie auth for humans, bearer tokens for agents
            </li>
            <li>
              <span className={`${styles.dot} dot`}>◆</span>
              SQLite by default · Postgres optional
            </li>
            <li>
              <span className={`${styles.dot} dot`}>◆</span>
              MCP-compatible · OpenTelemetry out of the box
            </li>
          </ul>
        </div>
        <div className={`${styles.pitchFoot} pitch-foot`}>
          v0.14.2 · commit a7c3f2e
        </div>
      </aside>
      <main className={`${styles.splitRight} split-right`}>
        <div className={styles.splitRightInner}>{children}</div>
      </main>
    </div>
  );
}
