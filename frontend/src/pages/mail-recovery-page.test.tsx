import "@testing-library/jest-dom/vitest"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, describe, expect, it, vi } from "vitest"
import { MailRecoveryPage } from "./mail-recovery-page"

const envelope = (data: unknown, message = "ok") => new Response(JSON.stringify({
  success: true,
  data,
  message,
  errors: [],
  meta: {},
}), { status: 200, headers: { "Content-Type": "application/json" } })

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MailRecoveryPage /></QueryClientProvider>)
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe("mail recovery page", () => {
  it("disables recovery until the account is frozen and the method is enabled", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(envelope([{
      id: 8,
      username: "active@example.com",
      enable: true,
      enable_auto_update_password: false,
      enable_delete_devices: false,
      enable_email_recovery: true,
      recovery_email: "rescue@example.com",
      mailbox_username: "rescue@example.com",
      recovery_methods: ["email_password"],
    }]))
    renderPage()

    const passwordActions = await screen.findAllByRole("button", { name: "邮件改密" })
    const rollbackActions = screen.getAllByRole("button", { name: "安全回退" })
    expect(passwordActions.every((button) => button.hasAttribute("disabled"))).toBe(true)
    expect(rollbackActions.every((button) => button.hasAttribute("disabled"))).toBe(true)
    expect(passwordActions[0]).toHaveAttribute("title", "请先停用账号、自动改密和设备操作")
  })

  it("confirms, polls a bounded recovery run, and loads history", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input)
      if (url.endsWith("/accounts?scope=admin")) return envelope([{
        id: 9,
        username: "frozen@example.com",
        enable: false,
        enable_auto_update_password: false,
        enable_delete_devices: false,
        enable_email_recovery: true,
        recovery_email: "rescue@example.com",
        mailbox_username: "rescue@example.com",
        recovery_methods: ["email_password", "email_security"],
      }])
      if (url.endsWith("/admin/accounts/9/recovery/email-password") && init?.method === "POST") {
        return envelope({ run_id: "0123456789abcdef0123456789abcdef", status: "running" }, "已提交")
      }
      if (url.endsWith("/admin/recoveries/0123456789abcdef0123456789abcdef")) {
        const statusCalls = fetchMock.mock.calls.filter(([called]) => String(called).includes("/admin/recoveries/")).length
        return envelope(statusCalls === 1
          ? { run_id: "0123456789abcdef0123456789abcdef", status: "running" }
          : { run_id: "0123456789abcdef0123456789abcdef", status: "success", message: "恢复完成" })
      }
      if (url.endsWith("/admin/accounts/9/recovery-history")) {
        return envelope([{ action: "email_password_recovery", status: "success", message: "恢复完成", created_at: "2026-09-08 20:00:00" }])
      }
      throw new Error(`unexpected request: ${url}`)
    })
    renderPage()

    const action = (await screen.findAllByRole("button", { name: "邮件改密" }))[0]
    fireEvent.click(action)
    expect(await screen.findByText("通过邮件重设密码？")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "确认提交" }))
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/admin/recoveries/"))).toBe(true))
    await act(async () => { vi.advanceTimersByTime(3000) })
    await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("/admin/recoveries/")).length).toBe(2))

    fireEvent.click(screen.getAllByRole("button", { name: "历史" })[0])
    expect(await screen.findByText("邮件恢复历史")).toBeInTheDocument()
    expect(await screen.findAllByText("恢复完成")).not.toHaveLength(0)
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/admin/accounts/9/recovery-history"))).toBe(true)
  })
})
