import "@testing-library/jest-dom/vitest"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { ListPage } from "./list-page"

const shareRows = [
  { id: 1, remark: "首个分享", share_link: "alpha01", account_count: 2, access_status: "unused", expiry_label: "永久有效" },
  { id: 2, remark: "第二个分享", share_link: "beta02", account_count: 1, access_status: "used", expiry_label: "首次访问后失效" },
]

const calls: Array<{ url: string; method: string; body: string | null }> = []

function envelope(data: unknown, meta: Record<string, unknown> = {}, message = "获取成功") {
  return new Response(JSON.stringify({ success: true, data, message, errors: [], meta }), { status: 200, headers: { "Content-Type": "application/json" } })
}

function mockApi() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input)
    calls.push({ url, method: (init?.method ?? "GET").toUpperCase(), body: typeof init?.body === "string" ? init.body : null })
    if (url.includes("/bootstrap")) return envelope({ authenticated: true, user: { username: "ops", is_admin: false, vip_expire_at: "2035-01-01 00:00:00" } })
    if (url.includes("shares/batch-delete")) return envelope(null, {}, "已删除 2 个分享链接")
    if (url.includes("/shares")) {
      const params = new URLSearchParams(url.split("?")[1] ?? "")
      return envelope(shareRows, { page: Number(params.get("page") ?? 1), per_page: Number(params.get("per_page") ?? 25), total: 120, last_page: 3, total_pages: 3 })
    }
    return envelope([])
  })
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><ListPage kind="shares" /></QueryClientProvider>)
}

const listCalls = () => calls.filter(call => call.url.includes("/shares?page="))

beforeEach(() => { window.localStorage.clear(); calls.length = 0 })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe("shares list", () => {
  it("requests the remembered page size and keeps it while paging", async () => {
    window.localStorage.setItem("panel:per-page:shares", "50")
    mockApi()
    renderPage()

    await screen.findAllByText("首个分享")
    expect(listCalls()[0].url).toContain("/shares?page=1&per_page=50")

    fireEvent.click(screen.getByLabelText("Go to next page"))
    await waitFor(() => expect(listCalls().some(call => call.url.includes("page=2&per_page=50"))).toBe(true))
  })

  it("defaults to 25 rows per page when nothing was stored", async () => {
    mockApi()
    renderPage()
    await screen.findAllByText("首个分享")
    expect(listCalls()[0].url).toContain("/shares?page=1&per_page=25")
    expect(screen.getByText("共 120 条 · 本页 2 条")).toBeInTheDocument()
  })

  it("searches all share pages through the API", async () => {
    mockApi()
    renderPage()
    await screen.findAllByText("首个分享")

    fireEvent.change(screen.getByRole("textbox", { name: "全局搜索分享标识、备注或分组" }), { target: { value: "第二组" } })

    await waitFor(() => expect(listCalls().some(call => call.url.includes("page=1&per_page=25&keyword=%E7%AC%AC%E4%BA%8C%E7%BB%84"))).toBe(true))
  })

  it("closes the batch delete dialog and refreshes the list after confirming", async () => {
    mockApi()
    renderPage()

    const checkboxes = await screen.findAllByLabelText("选择分享链接")
    fireEvent.click(checkboxes[0])
    fireEvent.click(screen.getByRole("button", { name: "批量删除" }))
    expect(screen.getByRole("alertdialog")).toBeVisible()

    fireEvent.click(screen.getByRole("button", { name: "确认删除" }))
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument())

    const deletion = calls.find(call => call.url.includes("shares/batch-delete"))
    expect(deletion?.method).toBe("POST")
    expect(deletion?.body).toContain("\"ids\":[1]")
  })
})
