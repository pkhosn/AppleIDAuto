import { expect, test } from "@playwright/test"

// All API requests are intercepted. Never submit mutations to a real Apple account.
test("independent cycles refill, convert units, save and show queue state", async ({ page }) => {
  const errors: string[] = []
  page.on("pageerror", e => errors.push(e.message))
  const features = {
    password_check: { source: "custom", minutes: 30, effective_minutes: 30 },
    password_update: { source: "custom", minutes: 120, effective_minutes: 120 },
    two_factor: { source: "custom", minutes: 30, effective_minutes: 30 },
    profile_restore: { source: "custom", minutes: 360, effective_minutes: 360 },
    device_cleanup: { source: "global", minutes: 30, effective_minutes: 30 },
  }
  const account = { id: 900001, username: "fixture@example.test", password: "fixture-not-real", owner: 1,
    enable: 1, enable_check_password_correct: 1, enable_auto_update_password: 1, enable_auto_disable_two_factor: 0,
    enable_auto_restore_profile: 0, recovery_methods: [], check_interval: 30, schedule: { active: true, mode: "custom", features } }
  let saved: Record<string, any> | null = null
  await page.route("**/frontend-api/v1/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname.replace("/frontend-api/v1/", "")
    let data: unknown = {}
    if (path === "bootstrap") data = { authenticated: true, user: { id: 1, username: "fixture-admin", is_admin: 1 }, csrf_token: "mock", admin_base: "/admin", branding: {}, registration: {} }
    else if (path === "accounts") data = [account]
    else if (path === "automation-defaults") data = { active: true, features: { password_check: 30, password_update: 1440, two_factor: 30, profile_restore: 360, device_cleanup: 30 } }
    else if (path.endsWith("/schedule")) data = { status: "pending", due: ["password_check"], features: { password_check: { result: "success", last_finished_at: "2026-09-05T00:00:00Z", next_run_at: "2026-09-05T00:30:00Z" } } }
    else if (path === "accounts/900001") {
      if (request.method() === "POST") saved = request.postDataJSON()
      else data = account
    }
    await route.fulfill({ json: { success: true, data, message: "已保存", errors: [], meta: { page: 1, total_pages: 1 } } })
  })
  await page.goto("./#/admin/accounts")
  await page.getByRole("button", { name: "编辑", exact: true }).first().click()
  const dialog = page.getByRole("dialog")
  await expect(dialog.getByLabel("Apple 账号", { exact: true })).toHaveValue("fixture@example.test")
  await expect(dialog.getByLabel("自动更新密码周期数值")).toHaveValue("2")
  await expect(dialog.getByText(/等待执行/)).toBeVisible()
  await dialog.getByLabel("自动更新密码周期数值").fill("3")
  await dialog.getByRole("checkbox", { name: "自动更新密码加入共用周期" }).check()
  await dialog.getByLabel("账号共用周期数值").fill("45")
  await dialog.getByRole("switch", { name: "共用任务周期" }).click()
  await expect(dialog.getByLabel("自动更新密码周期数值")).toHaveCount(0)
  await expect(dialog.getByText("账号共用周期 · 生效周期：45 分钟", { exact: true })).toBeVisible()
  await dialog.getByRole("switch", { name: "共用任务周期" }).click()
  await expect(dialog.getByLabel("自动更新密码周期数值")).toHaveValue("3")
  await dialog.getByRole("button", { name: "保存账号" }).click()
  await expect(dialog).not.toBeVisible()
  expect(saved).not.toBeNull()
  expect((saved as any).automation_schedule.shared).toEqual({ enabled: false, minutes: 45, features: ["password_update"] })
  expect((saved as any).automation_schedule).toMatchObject({ features: {
    password_update: { source: "custom", minutes: 180 },
    device_cleanup: { source: "global", minutes: 30 },
  } })
  expect((saved as any).username).toBe("fixture@example.test")
  expect(errors).toEqual([])
})

test("run-due action explains destructive-task safeguards", async ({ page }) => {
  let releaseRestart = () => undefined
  const restartGate = new Promise<void>(resolve => { releaseRestart = resolve })
  await page.route("**/frontend-api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith("tasks/900001/restart")) await restartGate
    const data = path.endsWith("bootstrap") ? { authenticated: true, user: { id: 1, is_admin: 1 }, csrf_token: "mock", admin_base: "/admin", branding: {}, registration: {} }
      : path.endsWith("tasks") ? [{ id: 900001, username: "first@example.test" }, { id: 900002, username: "second@example.test" }] : {}
    await route.fulfill({ json: { success: true, data, message: "ok", errors: [], meta: {} } })
  })
  await page.goto("./#/admin/tasks")
  await page.getByRole("button", { name: "运行到期项", exact: true }).first().click()
  await expect(page.getByRole("alertdialog")).toContainText("待确认的破坏性操作只会进入只读核验")
  await page.getByRole("button", { name: "确认运行到期项", exact: true }).click()
  await expect(page.getByRole("alertdialog")).not.toBeVisible()
  const pending = page.getByRole("button", { name: "提交中…", exact: true })
  await expect(pending).toBeVisible()
  await expect(pending.locator("svg")).toHaveClass(/animate-spin/)
  const idle = page.getByRole("button", { name: "运行到期项", exact: true })
  await expect(idle).toBeDisabled()
  await expect(idle.locator("svg")).not.toHaveClass(/animate-spin/)
  releaseRestart()
  await expect(page.getByRole("button", { name: "运行到期项", exact: true })).toHaveCount(2)
})

test("mobile account card includes the enable control", async ({ page }) => {
  test.skip(test.info().project.name !== "mobile", "移动端布局检查")
  await page.route("**/frontend-api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname
    const data = path.endsWith("bootstrap") ? { authenticated: true, user: { id: 1, is_admin: 1 }, csrf_token: "mock", admin_base: "/admin", branding: {}, registration: {} }
      : path.endsWith("accounts") ? [{ id: 900001, username: "mobile@example.test", enable: 0, task_status: "disabled", message: "fixture" }] : {}
    await route.fulfill({ json: { success: true, data, message: "ok", errors: [], meta: {} } })
  })
  await page.goto("./#/admin/accounts")
  await expect(page.getByRole("switch", { name: "启用账号" })).toBeVisible()
})
