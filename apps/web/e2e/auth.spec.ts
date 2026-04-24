import { expect, test } from "@playwright/test";

test("keeps the API token masked until the operator explicitly reveals it", async ({
  page,
}) => {
  await page.goto("/");

  await expect(
    page.getByRole("heading", {
      level: 1,
      name: "Paste an AgenticQueue API key",
    }),
  ).toBeVisible();

  const tokenInput = page.getByLabel("API token");
  await expect(tokenInput).toHaveAttribute("type", "password");
  await expect(tokenInput).toHaveAttribute("autocomplete", "off");
  await expect(tokenInput).toHaveAttribute("spellcheck", "false");
  await expect(tokenInput).toHaveAttribute("autocapitalize", "off");
  await expect(tokenInput).toHaveAttribute("inputmode", "text");

  await tokenInput.fill("aq__playwright_token");

  const toggle = page.getByRole("button", { name: /^show$/i });
  await toggle.click();

  await expect(tokenInput).toHaveAttribute("type", "text");
  await expect(tokenInput).toHaveValue("aq__playwright_token");
  await expect(page.getByRole("button", { name: /^hide$/i })).toHaveText(
    /hide/i,
  );
});
