import "@testing-library/jest-dom/vitest"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it, vi } from "vitest"
import { RestartTask } from "./restart-task"

afterEach(cleanup)

describe("RestartTask", () => {
  it("closes the confirmation dialog immediately after confirmation", async () => {
    const user = userEvent.setup()
    const confirm = vi.fn()
    render(<RestartTask disabled={false} onConfirm={confirm} />)
    await user.click(screen.getByRole("button", { name: "运行到期项" }))
    expect(screen.getByRole("alertdialog")).toBeVisible()
    await user.click(screen.getByRole("button", { name: "确认运行到期项" }))
    expect(confirm).toHaveBeenCalledOnce()
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument()
  })

  it("animates only a task explicitly marked pending", () => {
    render(<><RestartTask disabled={false} pending onConfirm={() => undefined} /><RestartTask disabled={true} onConfirm={() => undefined} /></>)
    const pending = screen.getByRole("button", { name: "提交中…" })
    const idle = screen.getByRole("button", { name: "运行到期项" })
    expect(pending.querySelector("svg")).toHaveClass("animate-spin")
    expect(idle.querySelector("svg")).not.toHaveClass("animate-spin")
  })
})
