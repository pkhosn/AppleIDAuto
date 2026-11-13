import { expect, test } from "@playwright/test"

test.use({ launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } })

test("account failure dialog displays and zooms a private screenshot on desktop and mobile", async ({ page }) => {
  await page.setContent('<main style="width:1100px;height:650px;background:#f5f5f7;padding:40px;box-sizing:border-box;font:24px sans-serif"><h1>Apple Account</h1><p>Authentication page failed to load</p><p>Screenshot fixture — no real account</p></main>')
  const png = await page.locator("main").screenshot()
  const account = { id: 2, username: "fixture@example.test", task_status: "failed", message: "Apple 登录认证未建立", enable: 0, last_check: "2026-11-13 12:00:00" }
  await page.route("**/frontend-api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith("failure-image")) {
      const expired = new URL(route.request().url()).searchParams.get("name") === "second.png"
      await route.fulfill({ status: expired ? 404 : 200, contentType: "image/png", body: expired ? Buffer.alloc(0) : png })
      return
    }
    let data: unknown = []
    if (path.endsWith("bootstrap")) data = { authenticated: true, user: { id: 1, username: "fixture", is_admin: false, vip_expire_at: "2035-01-01 00:00:00" }, csrf_token: "fixture", admin_base: "/admin", branding: {}, registration: {} }
    else if (path.endsWith("/failure")) data = { message: account.message, failed_at: account.last_check, images: [
      { name: "first.png", stage: "login", captured_at: account.last_check },
      { name: "second.png", stage: "password_form", captured_at: account.last_check },
    ] }
    else if (path.endsWith("/accounts")) data = [account]
    await route.fulfill({ json: { success: true, data, message: "获取成功", errors: [], meta: { total: 1 } } })
  })
  await page.goto("./#/accounts")
  await page.getByRole("button", { name: "失败原因", exact: true }).click()
  const dialog = page.getByRole("dialog")
  await expect(dialog.getByText(account.message, { exact: true })).toBeVisible()
  const image = dialog.getByAltText("失败截图：登录认证")
  await expect(image).toBeVisible()
  await expect.poll(() => image.evaluate(node => (node as HTMLImageElement).naturalWidth)).toBeGreaterThan(0)
  await dialog.getByRole("button", { name: "放大截图", exact: true }).click()
  await expect(dialog.getByRole("button", { name: "适应窗口", exact: true })).toBeVisible()
  expect(await dialog.evaluate(node => node.scrollWidth <= node.clientWidth + 1)).toBe(true)
  const bounds = await dialog.boundingBox()
  expect(bounds!.x).toBeGreaterThanOrEqual(0)
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(page.viewportSize()!.width + 1)
  await page.screenshot({ path: test.info().outputPath("failure-dialog.png"), fullPage: true })
  await dialog.getByLabel("选择失败截图").selectOption("second.png")
  await expect(dialog.getByText("截图已清理或暂时无法读取，失败原因仍可在上方查看。")).toBeVisible()
})
