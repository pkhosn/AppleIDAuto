import "@testing-library/jest-dom/vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it, vi } from "vitest"
import { AccountCredentials, DeleteAction, ShareEditor, pageTemplateChoices } from "./crud-editors"

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe("AccountCredentials", () => {
  it("reveals the stored credentials without asking for the panel password again", async () => {
    const calls: Array<{ url: string; method: string; body: string | null }> = []
    const promptSpy = vi.spyOn(window, "prompt").mockReturnValue("panel-password")
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      calls.push({ url: String(input), method: (init?.method ?? "GET").toUpperCase(), body: typeof init?.body === "string" ? init.body : null })
      return new Response(JSON.stringify({
        success: true, message: "凭据仅在本次响应中返回", errors: [], meta: {},
        data: {
          password: "ApplePass!23",
          question1: "第一所学校", answer1: "答案一",
          question2: "", answer2: "", question3: "", answer3: "",
          mailbox_password: "MailPass!45",
        },
      }), { status: 200, headers: { "Content-Type": "application/json" } })
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}>
      <AccountCredentials row={{ id: 5, username: "apple@example.com", recovery_email: "rescue@example.com" }} admin={false} />
    </QueryClientProvider>)

    fireEvent.click(screen.getByRole("button", { name: "查看密码" }))
    expect(await screen.findByDisplayValue("ApplePass!23")).toBeInTheDocument()
    expect(screen.getByDisplayValue("MailPass!45")).toBeInTheDocument()
    expect(screen.getByDisplayValue("答案一")).toBeInTheDocument()
    expect(screen.getByText("第一所学校")).toBeInTheDocument()
    expect(screen.getAllByDisplayValue("未记录").length).toBe(2)

    expect(promptSpy).not.toHaveBeenCalled()
    expect(calls[0].url).toContain("/accounts/5/reveal-password")
    expect(calls[0].method).toBe("POST")
    expect(calls[0].body ?? "").not.toContain("panel-password")
  })
})

describe("pageTemplateChoices", () => {
  it("lists exactly the managed templates and marks the effective default", () => {
    const { options, fallback } = pageTemplateChoices([
      { id: 1, name: "模板默认", effective_default: false },
      { id: 3, name: "小火箭下载教程", effective_default: true },
    ])
    expect(options).toEqual([
      { value: 1, label: "模板默认" },
      { value: 3, label: "小火箭下载教程（默认）" },
    ])
    expect(fallback).toBe(3)
  })

  it("stays empty instead of inventing an extra default entry", () => {
    const { options, fallback } = pageTemplateChoices([])
    expect(options).toEqual([])
    expect(fallback).toBe(0)
  })
})

describe("DeleteAction", () => {
  it("dismisses the confirmation dialog as soon as the deletion is confirmed", async () => {
    const user = userEvent.setup()
    const confirm = vi.fn()
    render(<DeleteAction title="删除选中的分享链接？" description="将删除 2 个分享链接，且无法恢复。" label="批量删除" onConfirm={confirm} />)

    await user.click(screen.getByRole("button", { name: "批量删除" }))
    expect(screen.getByRole("alertdialog")).toBeVisible()

    await user.click(screen.getByRole("button", { name: "确认删除" }))
    expect(confirm).toHaveBeenCalledOnce()
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument()
  })

  it("stays open until an operator confirms, and cancelling never deletes", async () => {
    const user = userEvent.setup()
    const confirm = vi.fn()
    render(<DeleteAction title="删除 Apple 账号？" onConfirm={confirm} />)

    await user.click(screen.getByRole("button", { name: "删除" }))
    expect(screen.getByRole("alertdialog")).toBeVisible()

    await user.click(screen.getByRole("button", { name: "取消" }))
    expect(confirm).not.toHaveBeenCalled()
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument()
  })
})


describe("ShareEditor embedding", () => {
  it("loads the origin policy, generates an embed URL and saves changed origins", async () => {
    let saved: Record<string, unknown> | undefined
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input)
      let data: unknown = []
      if (url.includes("bootstrap")) data = { user: { username: "fixture" } }
      else if (url.includes("shares/5")) {
        if (init?.method === "POST") {
          saved = JSON.parse(String(init.body))
          data = { share_link: "fixture" }
        } else data = { id: 5, share_link: "fixture", account_list: [1], embed_enabled: 1, embed_origins: "https://shop.example.com" }
      } else if (url.includes("accounts")) data = [{ id: 1, username: "fixture@example.invalid" }]
      return new Response(JSON.stringify({ success: true, message: "ok", data, errors: [], meta: {} }), { headers: { "Content-Type": "application/json" } })
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><ShareEditor row={{ id: 5, share_link: "fixture" }} admin={false} onSaved={() => {}} /></QueryClientProvider>)
    fireEvent.click(screen.getByRole("button", { name: "编辑" }))
    const origins = await screen.findByDisplayValue("https://shop.example.com")
    expect((screen.getByLabelText("iframe 嵌入代码（保存设置后生效）") as HTMLTextAreaElement).value).toContain("/share/fixture/embed")
    fireEvent.change(origins, { target: { value: "" } })
    fireEvent.click(screen.getByRole("button", { name: "保存分享" }))
    await vi.waitFor(() => expect(saved).toMatchObject({ embed_enabled: true, embed_origins: "" }))
  })
})
