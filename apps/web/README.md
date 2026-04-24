# AgenticQueue Web Shell

Read-only Next.js shell for the AgenticQueue UI surface. This app stays visible in every deployment mode per ADR-AQ-003, even when human approval is disabled.

## Supported Breakpoints

The current responsive audit and smoke coverage treat these viewport sizes as the supported breakpoint matrix:

| Label | Viewport | Typical device |
|---|---|---|
| `iphone-se` | `375 x 667` | Small phone portrait |
| `ipad-portrait` | `768 x 1024` | Tablet portrait |
| `ipad-landscape` | `1024 x 768` | Tablet landscape |
| `macbook` | `1440 x 900` | Laptop desktop |
| `desktop-fhd` | `1920 x 1080` | Full HD desktop |

## Responsive Audit

Run the responsive baseline sweep from the repo root:

```bash
npx playwright test apps/web/e2e/responsive-audit.spec.ts
```

Artifacts land in `apps/web/test-results/responsive-baseline/`:

- One full-page PNG per view and breakpoint.
- `audit-summary.json` with overflow flags and console/page error capture for each screenshot.
