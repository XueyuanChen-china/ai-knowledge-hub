import { test, expect } from "@playwright/test";

test("login reaches the protected workspace", async ({ page }) => {
  const email = process.env.E2E_EMAIL;
  const password = process.env.E2E_PASSWORD;

  test.skip(
    !email || !password,
    "Set E2E_EMAIL and E2E_PASSWORD to run the authenticated browser flow.",
  );

  await page.goto("/login");
  await page.getByLabel("邮箱").fill(email!);
  await page.getByLabel("密码").fill(password!);
  await page.getByRole("button", { name: "登录" }).click();

  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByText("知识库总览")).toBeVisible();
});
