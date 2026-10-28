import { expect, test, type APIRequestContext } from "@playwright/test"

async function adminBase(request: APIRequestContext): Promise<string> {
  const response = await request.get("/frontend-api/v1/bootstrap", { headers: { Accept: "application/json" } })
  expect(response.ok()).toBeTruthy()
  const body = await response.json()
  expect(body.success).toBe(true)
  expect(body.data.admin_base).toMatch(/^\/[a-zA-Z0-9_-]+$/)
  return body.data.admin_base
}

test("preview entry loads the React authentication shell", async ({ page }) => {
  await page.goto("./#/login")
  await expect(page.getByRole("heading", { name: "欢迎回来" })).toBeVisible()
  await expect(page.getByRole("button", { name: "进入工作台" })).toBeVisible()
  await expect(page.getByLabel("用户名")).toBeVisible()
})

test("formal user and dynamic admin URLs load the same application", async ({ page }) => {
  const managementPath = await adminBase(page.request)
  for (const path of ["/user/account", "/user/proxy", `${managementPath}/user`, `${managementPath}/proxy`]) {
    await page.goto(path)
    await expect(page.getByRole("heading", { name: "欢迎回来" })).toBeVisible()
  }
})

test("frontend API uses the normalized envelope without exposing secrets", async ({ request }) => {
  const response = await request.get("/frontend-api/v1/bootstrap", { headers: { Accept: "application/json" } })
  expect(response.ok()).toBeTruthy()
  const body = await response.json()
  expect(body).toMatchObject({ success: true, data: { authenticated: false } })
  expect(body.data.registration).not.toHaveProperty("turnstile_secret_key")
})

test("authenticated navigation smoke test", async ({ page }) => {
  test.skip(!process.env.E2E_USERNAME || !process.env.E2E_PASSWORD, "需要 E2E_USERNAME/E2E_PASSWORD")
  await page.goto("./#/login")
  await page.getByLabel("用户名").fill(process.env.E2E_USERNAME!)
  await page.getByLabel("密码").fill(process.env.E2E_PASSWORD!)
  const loginResponse = page.waitForResponse(response => response.url().includes("/frontend-api/v1/auth/login"))
  await page.getByRole("button", { name: "进入工作台" }).click()
  expect((await loginResponse).ok()).toBeTruthy()
  await expect(page.getByText("今天的运行情况")).toBeVisible()
  await page.getByRole("link", { name: "代理网络" }).click()
  await expect(page.getByRole("heading", { name: "代理网络" })).toBeVisible()
})

test("administrator can open the dynamic management path", async ({ page }) => {
  test.skip(!process.env.E2E_USERNAME || !process.env.E2E_PASSWORD || process.env.E2E_ADMIN !== "1", "需要临时管理员")
  await page.goto("/")
  await page.getByLabel("用户名").fill(process.env.E2E_USERNAME!)
  await page.getByLabel("密码").fill(process.env.E2E_PASSWORD!)
  await page.getByRole("button", { name: "进入工作台" }).click()
  await expect(page.getByText("今天的运行情况")).toBeVisible()
  await page.goto(`${await adminBase(page.request)}/user`)
  await expect(page.getByRole("heading", { name: "用户与权限" })).toBeVisible()
})

test("account menu exposes a working logout action", async ({ page }) => {
  test.skip(test.info().project.name !== "desktop" || !process.env.E2E_USERNAME || !process.env.E2E_PASSWORD, "需要桌面端临时账号")
  await page.goto("/")
  await page.getByLabel("用户名").fill(process.env.E2E_USERNAME!)
  await page.getByLabel("密码").fill(process.env.E2E_PASSWORD!)
  await page.getByRole("button", { name: "进入工作台" }).click()
  await expect(page.getByText("今天的运行情况")).toBeVisible()
  await page.getByText(process.env.E2E_USERNAME!, { exact: true }).click()
  const logout = page.getByRole("menuitem", { name: "安全退出" })
  await expect(logout).toBeVisible()
  await logout.click()
  await expect(page.getByRole("heading", { name: "欢迎回来" })).toBeVisible()
})

test("account editor loads the selected asset instead of login autofill", async ({ page }) => {
  test.skip(test.info().project.name !== "desktop" || process.env.E2E_ADMIN !== "1" || !process.env.E2E_USERNAME || !process.env.E2E_PASSWORD || !process.env.E2E_ACCOUNT_USERNAME, "需要管理员和目标账号")
  await page.goto("/")
  await page.getByLabel("用户名").fill(process.env.E2E_USERNAME!)
  await page.getByLabel("密码").fill(process.env.E2E_PASSWORD!)
  await page.getByRole("button", { name: "进入工作台" }).click()
  await expect(page.getByText("今天的运行情况")).toBeVisible()
  await page.goto(`${await adminBase(page.request)}/account`)
  const accountRow = page.getByRole("row").filter({ hasText: process.env.E2E_ACCOUNT_USERNAME! })
  await accountRow.getByRole("button", { name: "编辑" }).click()
  await expect(page.getByLabel("Apple 账号")).toHaveValue(process.env.E2E_ACCOUNT_USERNAME!)
  await expect(page.getByLabel("恢复邮箱")).toHaveValue(process.env.E2E_ACCOUNT_RECOVERY_EMAIL ?? "")
  await expect(page.getByLabel("IMAP 主机")).toHaveValue(process.env.E2E_ACCOUNT_IMAP_HOST ?? "")
  await expect(page.getByLabel("账号密码")).not.toHaveValue("")
  await expect(page.getByLabel("邮箱密码")).not.toHaveValue("")
})
