import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { expect, test, type Page } from "@playwright/test";

import { mockShellReadApis, seedAuthenticatedSession } from "./helpers";

const AQ_321_VIEWPORT = { width: 1440, height: 900 } as const;
const FOOTER_SINGLE_ROW_TOLERANCE_PX = 4;
const FOOTER_SINGLE_ROW_MAX_HEIGHT_PX = 64;
const DESIGN_REFERENCE_HTML =
  process.env.AQ_DESIGN_REFERENCE_HTML ??
  "D:/mmmmm/mmmmm-agenticqueue/site/app-preview/project/AgenticQueue-standalone.html";
const DESIGN_REFERENCE_URL = pathToFileURL(DESIGN_REFERENCE_HTML).href;
const ARTIFACT_DIR = path.resolve(__dirname, "..", "test-results", "aq-321");
const MANIFEST_PATH = path.join(ARTIFACT_DIR, "manifest.json");
const COMPARISON_PATH = path.join(ARTIFACT_DIR, "index.html");

const PARITY_VIEWS = [
  {
    name: "pipelines",
    productPath: "/pipelines",
    productHeading: "Pipelines",
    designButton: "Pipelines",
  },
  {
    name: "work",
    productPath: "/work",
    productHeading: "Work",
    designButton: "Work",
  },
  {
    name: "decisions",
    productPath: "/decisions",
    productHeading: "Decisions",
    designButton: "Decisions",
  },
  {
    name: "learnings",
    productPath: "/learnings",
    productHeading: "Learnings",
    designButton: "Learnings",
  },
] as const;

test("footer-single-row renders health pills and settings link", async ({
  page,
}) => {
  await page.setViewportSize(AQ_321_VIEWPORT);
  await mockProductShell(page);
  await page.goto("/pipelines");

  const footer = page.locator(".aq-footer");
  await expect(footer).toBeVisible();
  await expect(footer.locator(".aq-footer-left")).toBeVisible();
  await expect(footer.locator(".aq-footer-right")).toBeVisible();
  await expect(footer.locator(".aq-footer-left .aq-footer-pill")).toHaveCount(3);
  await expect(footer.getByRole("link", { name: /^Settings$/i })).toBeVisible();

  const layout = await footer.evaluate((element) => {
    const left = element.querySelector<HTMLElement>(".aq-footer-left");
    const right = element.querySelector<HTMLElement>(".aq-footer-right");
    const rect = element.getBoundingClientRect();
    const leftRect = left?.getBoundingClientRect();
    const rightRect = right?.getBoundingClientRect();

    return {
      footerHeight: rect.height,
      rowDelta:
        leftRect && rightRect
          ? Math.abs(leftRect.top - rightRect.top) +
            Math.abs(leftRect.height - rightRect.height)
          : Number.POSITIVE_INFINITY,
    };
  });

  expect(layout.footerHeight).toBeLessThanOrEqual(FOOTER_SINGLE_ROW_MAX_HEIGHT_PX);
  expect(layout.rowDelta).toBeLessThanOrEqual(FOOTER_SINGLE_ROW_TOLERANCE_PX);
});

test("aq-321-parity captures product and design screenshot pairs", async ({
  page,
}) => {
  test.setTimeout(120_000);

  await fs.mkdir(ARTIFACT_DIR, { recursive: true });
  await page.setViewportSize(AQ_321_VIEWPORT);
  await mockProductShell(page);

  const manifest: Array<{
    view: string;
    product: string;
    design: string;
    viewport: typeof AQ_321_VIEWPORT;
  }> = [];

  for (const view of PARITY_VIEWS) {
    const productPath = path.join(ARTIFACT_DIR, `${view.name}-product.png`);
    await page.goto(view.productPath);
    await expect(
      page.getByRole("heading", { level: 1, name: view.productHeading }),
    ).toBeVisible();
    await expect(page.locator(".aq-footer")).toBeVisible();
    await hideDynamicChrome(page);
    await page.screenshot({ path: productPath });

    const designPath = path.join(ARTIFACT_DIR, `${view.name}-design.png`);
    await page.goto(DESIGN_REFERENCE_URL);
    await waitForDesignShell(page);
    await selectDesignView(page, view.designButton);
    await hideDynamicChrome(page);
    await page.screenshot({ path: designPath });

    manifest.push({
      view: view.name,
      product: productPath,
      design: designPath,
      viewport: AQ_321_VIEWPORT,
    });
  }

  await fs.writeFile(MANIFEST_PATH, JSON.stringify(manifest, null, 2), "utf8");
  await fs.writeFile(COMPARISON_PATH, buildComparisonHtml(manifest), "utf8");
  await expect.poll(() => fs.stat(MANIFEST_PATH).then((stat) => stat.size)).toBeGreaterThan(0);
  await expect.poll(() => fs.stat(COMPARISON_PATH).then((stat) => stat.size)).toBeGreaterThan(0);
});

async function mockProductShell(page: Page) {
  await seedAuthenticatedSession(page);
  await mockShellReadApis(page);
  await page.route("**/api/v1/nav-counts", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: {
        pipelines: 2,
        work: 2,
        analytics: 6,
        graph: 4,
        decisions: 2,
        learnings: 3,
      },
      status: 200,
    });
  });
  await page.route("**/api/health", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: {
        status: "ok",
        deps: {
          api: {
            status: "ok",
            http_status: 200,
          },
        },
      },
      status: 200,
    });
  });
}

async function waitForDesignShell(page: Page) {
  await page.waitForSelector(".aq-app", { state: "visible", timeout: 20_000 });
  await page.locator("#__bundler_err").waitFor({ state: "detached", timeout: 1_000 }).catch(() => undefined);
}

async function selectDesignView(page: Page, label: string) {
  const button = page.getByRole("button", { name: new RegExp(`^${label}\\b`) });
  if (await button.count()) {
    await button.first().click();
  }
  await expect(page.locator(".aq-footer")).toBeVisible();
}

async function hideDynamicChrome(page: Page) {
  await page.addStyleTag({
    content:
      "nextjs-portal, #__bundler_loading, #__bundler_thumbnail, .aq-tweaks { display: none !important; }",
  });
}

function buildComparisonHtml(
  pairs: Array<{
    view: string;
    product: string;
    design: string;
    viewport: typeof AQ_321_VIEWPORT;
  }>,
) {
  const rows = pairs
    .map((pair) => {
      const productName = path.basename(pair.product);
      const designName = path.basename(pair.design);
      return `<section>
  <h2>${pair.view}</h2>
  <div class="pair">
    <figure><figcaption>Product</figcaption><img src="${productName}" alt="${pair.view} product screenshot"></figure>
    <figure><figcaption>Design reference</figcaption><img src="${designName}" alt="${pair.view} design screenshot"></figure>
  </div>
</section>`;
    })
    .join("\n");

  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>AQ-321 parity screenshots</title>
  <style>
    body { margin: 24px; background: #111827; color: #f3f4f6; font: 14px/1.45 system-ui, sans-serif; }
    section { margin-bottom: 32px; }
    h1, h2 { margin: 0 0 12px; }
    .pair { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
    figure { margin: 0; border: 1px solid #273244; background: #0d1117; }
    figcaption { padding: 8px 10px; border-bottom: 1px solid #273244; color: #98a2b3; font: 12px ui-monospace, monospace; }
    img { display: block; width: 100%; height: auto; }
  </style>
</head>
<body>
  <h1>AQ-321 parity screenshots (${AQ_321_VIEWPORT.width}x${AQ_321_VIEWPORT.height})</h1>
  ${rows}
</body>
</html>`;
}
