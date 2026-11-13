import "@testing-library/jest-dom/vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, describe, expect, it, vi } from "vitest"
import { AccountFailure } from "./account-failure"

const row = { id: 2, username: "fixture@example.test", task_status: "failed", message: "Apple 登录失败", last_check: "2026-11-13 12:00:00" }
function renderFailure(status = "failed", admin = false) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AccountFailure row={{ ...row, task_status: status }} admin={admin}/></QueryClientProvider>)
}
function mockDetails(images: Array<{name: string; stage: string; captured_at: string}> = []) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ success: true, data: { message: "Apple 未提供密码表单", failed_at: row.last_check, images } }), { status: 200 }))
}
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe("account failure", () => {
  it("loads lazily, preserves reason without screenshots, and scopes admin requests", async () => {
    const fetch = mockDetails()
    renderFailure("failed", true)
    expect(fetch).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole("button", { name: "失败原因" }))
    expect(await screen.findByText("Apple 未提供密码表单")).toBeInTheDocument()
    expect(await screen.findByText(/暂无可用截图/)).toBeInTheDocument()
    expect(fetch.mock.calls[0][0]).toBe("/frontend-api/v1/accounts/2/failure?scope=admin")
  })
  it("switches screenshots, supports zoom and explains expired images", async () => {
    mockDetails([{name:"first.png",stage:"login",captured_at:row.last_check},{name:"second.png",stage:"password_form",captured_at:row.last_check}])
    renderFailure()
    fireEvent.click(screen.getByRole("button", { name: "失败原因" }))
    const image = await screen.findByAltText("失败截图：登录认证")
    expect(image).toHaveAttribute("src", "/frontend-api/v1/accounts/2/failure-image?name=first.png")
    fireEvent.click(screen.getByRole("button", { name: "放大截图" }))
    expect(screen.getByRole("button", { name: "适应窗口" })).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText("选择失败截图"), { target: { value: "second.png" } })
    const second = await screen.findByAltText("失败截图：密码表单")
    fireEvent.error(second)
    expect(await screen.findByText(/截图已清理或暂时无法读取/)).toBeInTheDocument()
    expect(screen.getByText("Apple 未提供密码表单")).toBeInTheDocument()
  })
  it("hides the entry after success without requesting evidence", () => {
    const fetch = mockDetails()
    renderFailure("success")
    expect(screen.queryByRole("button", { name: "失败原因" })).not.toBeInTheDocument()
    expect(fetch).not.toHaveBeenCalled()
  })
})
