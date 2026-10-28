import "@testing-library/jest-dom/vitest"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, describe, expect, it, vi } from "vitest"
import { NotificationsPage } from "./notifications-page"

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe("notifications page", () => {
  it("loads and saves the Telegram result preference through the dedicated API", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({
        success: true,
        data: { tg_bot_token: "token", tg_chat_id: "123", tg_notify_mode: "all", wx_pusher_id: "", webhook: "" },
        message: "ok", errors: [], meta: {},
      }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ success: true, data: null, message: "已保存", errors: [], meta: {} }), {
        status: 200, headers: { "Content-Type": "application/json" },
      }))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><NotificationsPage /></QueryClientProvider>)

    const mode = await screen.findByLabelText("任务结果推送")
    expect(mode).toHaveValue("all")
    fireEvent.change(mode, { target: { value: "failure" } })
    fireEvent.click(screen.getByRole("button", { name: "保存推送设置" }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    expect(fetchMock.mock.calls[0][0]).toBe("/frontend-api/v1/notifications")
    expect(fetchMock.mock.calls[1][0]).toBe("/frontend-api/v1/notifications")
    expect(JSON.parse(String((fetchMock.mock.calls[1][1] as RequestInit).body))).toMatchObject({ tg_notify_mode: "failure" })
  })
})
