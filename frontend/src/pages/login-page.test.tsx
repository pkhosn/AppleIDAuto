import "@testing-library/jest-dom/vitest"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { LoginPage } from "./login-page"
import type { BootstrapData } from "@/types"

const bootstrap = {
  authenticated: false,
  user: null,
  csrf_token: "fixture-csrf",
  admin_base: "/fixture-admin",
  branding: {},
  registration: {
    registration_enabled: true,
    turnstile_enabled: true,
    turnstile_site_key: "fixture-site-key",
  },
} satisfies BootstrapData

afterEach(() => {
  cleanup()
  document.querySelectorAll("script[data-react-turnstile]").forEach(script => script.remove())
  delete window.turnstile
  vi.useRealTimers()
})

describe("login Turnstile", () => {
  it("renders after the Cloudflare script becomes available", async () => {
    const renderWidget = vi.fn((_element: HTMLElement, options: Record<string, unknown>) => {
      ;(options.callback as (token: string) => void)("fixture-token")
      return "fixture-widget"
    })
    render(<LoginPage bootstrap={bootstrap} onAuthenticated={vi.fn()} />)
    const script = document.querySelector<HTMLScriptElement>("script[data-react-turnstile]")
    expect(script).not.toBeNull()
    window.turnstile = { render: renderWidget, remove: vi.fn() }
    fireEvent.load(script!)
    await waitFor(() => expect(renderWidget).toHaveBeenCalledOnce())
    expect(screen.queryByText("正在加载或等待人机验证…")).not.toBeInTheDocument()
  })

  it("shows a retry action and replaces a failed script", async () => {
    render(<LoginPage bootstrap={bootstrap} onAuthenticated={vi.fn()} />)
    const first = document.querySelector<HTMLScriptElement>("script[data-react-turnstile]")!
    fireEvent.error(first)
    expect(await screen.findByRole("button", { name: "重新加载" })).toBeVisible()
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }))
    await waitFor(() => {
      const current = document.querySelector<HTMLScriptElement>("script[data-react-turnstile]")
      expect(current).not.toBe(first)
    })
    expect(screen.getByText("正在加载或等待人机验证…")).toBeVisible()
  })

  it("offers retry when a rendered widget never returns a token", async () => {
    vi.useFakeTimers()
    window.turnstile = { render: vi.fn(() => "empty-widget"), remove: vi.fn() }
    render(<LoginPage bootstrap={bootstrap} onAuthenticated={vi.fn()} />)
    await act(async () => { vi.advanceTimersByTime(10_000) })
    expect(screen.getByRole("button", { name: "重新加载" })).toBeVisible()
  })
})
