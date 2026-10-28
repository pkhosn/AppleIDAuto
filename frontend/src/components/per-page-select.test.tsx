import "@testing-library/jest-dom/vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest"
import { DEFAULT_PER_PAGE, PER_PAGE_OPTIONS, PerPageSelect, usePerPage } from "./per-page-select"

function Harness({ scopeKey = "fixture", onValue }: { scopeKey?: string; onValue?: (value: number) => void }) {
  const [perPage, setPerPage] = usePerPage(scopeKey)
  return <>
    <PerPageSelect value={perPage} onChange={next => { setPerPage(next); onValue?.(next) }} />
    <span data-testid="current">{perPage}</span>
  </>
}

beforeEach(() => window.localStorage.clear())
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe("page size selection", () => {
  it("defaults to 25 rows and exposes the shared page sizes", () => {
    render(<Harness />)
    expect(screen.getByTestId("current")).toHaveTextContent(String(DEFAULT_PER_PAGE))
    expect(screen.getByRole("combobox", { name: "每页行数" })).toHaveTextContent("25 条")
    expect([...PER_PAGE_OPTIONS]).toEqual([10, 25, 50, 100])
  })

  it("hydrates from storage, falls back for unsupported values, and persists choices", () => {
    const change = vi.fn()
    window.localStorage.setItem("panel:per-page:shares", "50")
    render(<Harness scopeKey="shares" onValue={change} />)
    expect(screen.getByTestId("current")).toHaveTextContent("50")

    window.localStorage.setItem("panel:per-page:orders", "37")
    cleanup()
    render(<Harness scopeKey="orders" />)
    expect(screen.getByTestId("current")).toHaveTextContent(String(DEFAULT_PER_PAGE))
    expect(window.localStorage.getItem("panel:per-page:orders")).toBe(String(DEFAULT_PER_PAGE))
  })

  it("keeps the page size of each list independent", () => {
    window.localStorage.setItem("panel:per-page:accounts", "10")
    render(<Harness scopeKey="accounts" />)
    expect(screen.getByTestId("current")).toHaveTextContent("10")

    cleanup()
    render(<Harness scopeKey="proxy-static" />)
    expect(screen.getByTestId("current")).toHaveTextContent(String(DEFAULT_PER_PAGE))
  })

  it("reports the requested size to the owning list", () => {
    const change = vi.fn()
    render(<Harness scopeKey="shares" onValue={change} />)
    const trigger = screen.getByRole("combobox", { name: "每页行数" })
    expect(trigger).toBeVisible()
    expect(trigger).toHaveTextContent("25 条")
  })
})
