import "@testing-library/jest-dom/vitest"
import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, describe, expect, it, vi } from "vitest"
import { AccountCycles, cycleDefinitions, defaultCycles, formatMinutes, IntervalInput, minuteSchema, validCycles } from "./automation-cycles"

afterEach(() => { cleanup(); vi.restoreAllMocks() })
describe("independent automation cycles", () => {
  it("validates integer minutes and the 30-day maximum", () => {
    for (const value of [1, 30, 1440, 43200]) expect(minuteSchema.safeParse(value).success).toBe(true)
    for (const value of [0, -1, 43201, 1.5, NaN]) expect(minuteSchema.safeParse(value).success).toBe(false)
  })
  it("new accounts inherit independent global defaults", () => {
    expect(validCycles(defaultCycles)).toBe(true)
    expect(Object.values(defaultCycles).every(v => v.source === "global")).toBe(true)
    expect(defaultCycles.password_update.minutes).toBe(1440)
    expect(defaultCycles.profile_restore.minutes).toBe(360)
    expect(defaultCycles.device_cleanup.minutes).toBe(30)
  })
  it("keeps independent source and custom values for every feature", () => {
    const cycles = structuredClone(defaultCycles)
    cycles.password_update.minutes = 180
    cycles.password_update.source = "custom"
    expect(cycles.password_update).toEqual({ source: "custom", minutes: 180 })
    expect(cycles.password_check.source).toBe("global")
  })
  it("formats units without changing cadence", () => {
    expect(formatMinutes(1440)).toBe("1 天")
    expect(formatMinutes(360)).toBe("6 小时")
    expect(formatMinutes(30)).toBe("30 分钟")
  })
  it("converts custom hours to minutes", () => {
    const change = vi.fn()
    render(<IntervalInput label="测试" minutes={120} onChange={change} />)
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "3" } })
    expect(change).toHaveBeenCalledWith(180)
  })
  it("selects shared membership without changing feature flags or original cadence", async () => {
    const change = vi.fn()
    const sharedChange = vi.fn(), setFlag = vi.fn()
    const flags = Object.fromEntries(cycleDefinitions.map(([, , flag]) => [flag, true]))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><AccountCycles cycles={structuredClone(defaultCycles)} onChange={change}
      shared={{ enabled: false, minutes: 45, features: [] }} onSharedChange={sharedChange}
      flags={flags} setFlag={setFlag} admin={false} open={false} /></QueryClientProvider>)
    expect(screen.queryByRole("switch", { name: "自动更新密码使用全局时间" })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole("checkbox", { name: "自动更新密码加入共用周期" }))
    expect(sharedChange).toHaveBeenCalledWith({ enabled: false, minutes: 45, features: ["password_update"] })
    expect(change).not.toHaveBeenCalled()
    expect(setFlag).not.toHaveBeenCalled()
  })
  it("shows the run timeline and can run one feature", async () => {
    const calls: Array<[string, RequestInit | undefined]> = []
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input)
      calls.push([url, init])
      if (url.includes("automation-defaults")) return new Response(JSON.stringify({
        success: true, data: { active: true, features: Object.fromEntries(cycleDefinitions.map(([key, , , minutes]) => [key, minutes])) }, message: "ok", errors: [], meta: {},
      }), { status: 200, headers: { "Content-Type": "application/json" } })
      if (url.includes("/schedule")) return new Response(JSON.stringify({
        success: true, data: {
          status: "success", due: [], features: { password_check: { result: "success" } },
          runs: [{ run_id: "abcdef123456", status: "success", stage: "completed", queued_at: new Date().toISOString(), started_at: new Date().toISOString(), finished_at: new Date().toISOString(), retry_decision: "none", steps: [{ feature: "password_check", status: "success", stage: "login", duration_ms: 10 }] }],
        }, message: "ok", errors: [], meta: {},
      }), { status: 200, headers: { "Content-Type": "application/json" } })
      return new Response(JSON.stringify({ success: true, data: null, message: "已进入队列", errors: [], meta: {} }), { status: 200, headers: { "Content-Type": "application/json" } })
    })
    const flags = Object.fromEntries(cycleDefinitions.map(([, , flag]) => [flag, true]))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><AccountCycles cycles={structuredClone(defaultCycles)} onChange={() => undefined}
      flags={flags} setFlag={() => undefined} accountId={1} admin={false} open /></QueryClientProvider>)
    expect(await screen.findByText("运行 abcdef12")).toBeVisible()
    await userEvent.click((await screen.findAllByRole("button", { name: "单独运行此项" }))[0])
    await waitFor(() => expect(calls.some(([url, init]) => url.includes("tasks/1/restart") && init?.method === "POST")).toBe(true))
    const restart = calls.find(([url]) => url.includes("tasks/1/restart"))
    expect(JSON.parse(String(restart?.[1]?.body))).toMatchObject({ mode: "feature", feature: "password_check" })
  })
})
