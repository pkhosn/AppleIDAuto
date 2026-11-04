import "@testing-library/jest-dom/vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it, vi } from "vitest"
import { AccountCredentials, CopyAccountInfo, SecurityQuestionSelect, DeleteAction, ShareEditor, ShareGroupManager, CopyShare, pageTemplateChoices } from "./crud-editors"

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe("account information controls", () => {
  it("offers six questions per group and preserves existing custom questions", () => {
    const change = vi.fn()
    render(<SecurityQuestionSelect index={0} value="旧问题" onChange={change} />)
    const select = screen.getByRole("combobox", { name: "密保问题 1" })
    expect(select).toHaveValue("旧问题")
    expect(screen.getAllByRole("option")).toHaveLength(8)
    fireEvent.change(select, { target: { value: "你的第一个宠物叫什么名字?" } })
    expect(change).toHaveBeenCalledWith("你的第一个宠物叫什么名字?")
  })

  it("copies the requested six fields through the scoped credential endpoint", async () => {
    const user = userEvent.setup()
    const write = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue()
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      success: true, data: { password: "FixturePass!", answer1: "小明", answer2: "医生", answer3: "北京", mailbox_password: "NeverCopyMail" },
    }), { status: 200, headers: { "Content-Type": "application/json" } }))
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
    render(<QueryClientProvider client={client}><CopyAccountInfo row={{ id: 5, username: "fixture@example.test", dob: "2000-01-02" }} admin /></QueryClientProvider>)
    await user.click(screen.getByRole("button", { name: "fixture@example.test" }))
    await vi.waitFor(() => expect(write).toHaveBeenCalledWith("账号fixture@example.test 密码FixturePass! 朋友小明 工作医生 父母北京 生日2000-01-02"))
    expect(String(fetch.mock.calls[0][0])).toContain("/accounts/5/reveal-password?scope=admin")
  })
})

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
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  })
})


describe("share management save behavior", () => {
  function mockShareApi(fail=false) {
    const saved: Record<string,unknown>[]=[]
    vi.spyOn(globalThis,"fetch").mockImplementation(async(input,init)=>{
      const url=String(input); let data:unknown=[]
      if(init?.method==="POST") {
        saved.push(JSON.parse(String(init.body)))
        return new Response(JSON.stringify({success:!fail,message:fail?"保存失败":"ok",data:{share_link:"fixture"}}),{status:fail?422:200,headers:{"Content-Type":"application/json"}})
      }
      if(url.includes("bootstrap")) data={user:{username:"fixture"}}
      else if(url.includes("share-groups")) data=[{id:7,name:"已有分组",account_list:[1],password_configured:true}]
      else if(url.includes("accounts")) data=[{id:1,username:"fixture@test.invalid"}]
      return new Response(JSON.stringify({success:true,data}),{headers:{"Content-Type":"application/json"}})
    })
    return saved
  }
  it("starts passwords off, requires content when enabled and closes a newly created share",async()=>{
    const saved=mockShareApi();const onSaved=vi.fn()
    const client=new QueryClient({defaultOptions:{queries:{retry:false}}})
    render(<QueryClientProvider client={client}><ShareEditor admin onSaved={onSaved}/></QueryClientProvider>)
    fireEvent.click(screen.getByRole("button",{name:"新建分享"}))
    await screen.findByText("fixture@test.invalid")
    expect(screen.queryByText("当前用户")).not.toBeInTheDocument()
    expect(screen.getByRole("switch",{name:"访问密码"})).not.toBeChecked()
    expect(screen.queryByLabelText("访问密码内容")).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("checkbox"))
    fireEvent.click(screen.getByRole("switch",{name:"访问密码"}))
    expect(screen.getByRole("button",{name:"保存分享"})).toBeDisabled()
    fireEvent.change(screen.getByLabelText("访问密码内容"),{target:{value:"test-pass"}})
    fireEvent.click(screen.getByRole("button",{name:"保存分享"}))
    await vi.waitFor(()=>expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
    expect(saved[0]).toMatchObject({password_enabled:true,password:"test-pass"})
    expect(onSaved).toHaveBeenCalledOnce()
  })
  it.each([false,true])("offers explicit group edit, and closes only on successful save (failure=%s)",async(fail)=>{
    const saved=mockShareApi(fail);const onSaved=vi.fn()
    const client=new QueryClient({defaultOptions:{queries:{retry:false}}})
    render(<QueryClientProvider client={client}><ShareGroupManager admin onSaved={onSaved}/></QueryClientProvider>)
    fireEvent.click(screen.getByRole("button",{name:"分享组"}))
    fireEvent.click(await screen.findByRole("button",{name:"编辑"}))
    expect(screen.getByLabelText("组名称")).toHaveValue("已有分组")
    expect(screen.getByRole("switch",{name:"访问密码"})).toBeChecked()
    fireEvent.click(screen.getByRole("switch",{name:"访问密码"}))
    fireEvent.click(screen.getByRole("button",{name:"保存并同步组内链接"}))
    await vi.waitFor(()=>expect(saved).toHaveLength(1))
    expect(saved[0]).toMatchObject({password_enabled:false})
    if(fail) {
      await vi.waitFor(()=>expect(screen.getByRole("button",{name:"保存并同步组内链接"})).toBeEnabled())
      expect(screen.getByRole("dialog")).toBeInTheDocument()
      expect(screen.getByLabelText("组名称")).toHaveValue("已有分组")
      expect(onSaved).not.toHaveBeenCalled()
    } else {
      await vi.waitFor(()=>expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
      expect(onSaved).toHaveBeenCalledOnce()
    }
  })
})


describe("CopyShare",()=>{
  it.each(["https://example.test/share/fixture","分享链接：https://example.test/share/fixture\n访问密码：FixturePass"])("copies the server-generated share text: %s",async(text)=>{
    const user=userEvent.setup()
    const write=vi.spyOn(navigator.clipboard,"writeText").mockResolvedValue()
    const fetch=vi.spyOn(globalThis,"fetch").mockResolvedValue(new Response(JSON.stringify({success:true,data:{text}}),{headers:{"Content-Type":"application/json"}}))
    const client=new QueryClient()
    render(<QueryClientProvider client={client}><CopyShare id={7} admin/></QueryClientProvider>)
    await user.click(screen.getByRole("button",{name:"复制分享"}))
    await vi.waitFor(()=>expect(write).toHaveBeenCalledWith(text))
    expect(String(fetch.mock.calls[0][0])).toContain("shares/7/copy?scope=admin")
    expect(fetch.mock.calls[0][1]?.method).toBe("POST")
  })
})
