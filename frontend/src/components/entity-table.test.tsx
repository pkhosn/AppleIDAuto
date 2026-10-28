import "@testing-library/jest-dom/vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"
import { EntityTable, statusNode } from "@/components/entity-table"

describe("EntityTable",()=>{
  const rows=[{id:1,username:"alpha@example.com",status:"running"},{id:2,username:"beta@example.com",status:"failed"}]
  it("renders compact rows and filters them",async()=>{
    const user=userEvent.setup()
    render(<EntityTable rows={rows} columns={[{key:"username",label:"账号"},{key:"status",label:"状态"}]}/>)
    expect(screen.getAllByText("alpha@example.com").length).toBeGreaterThan(0)
    await user.type(screen.getByRole("textbox",{name:"搜索当前列表"}),"beta")
    expect(screen.queryByText("alpha@example.com")).not.toBeInTheDocument()
    expect(screen.getAllByText("beta@example.com").length).toBeGreaterThan(0)
  })

  it("shows an empty state",()=>{
    render(<EntityTable rows={[]} columns={[{key:"username",label:"账号"}]}/>)
    expect(screen.getByText("暂无数据")).toBeInTheDocument()
  })

  it("translates numeric status values",()=>{
    render(<EntityTable rows={[{id:1,status:1},{id:2,status:0}]} columns={[{key:"status",label:"状态",render:statusNode}]}/>)
    expect(screen.getAllByText("已启用").length).toBeGreaterThan(0)
    expect(screen.getAllByText("已停用").length).toBeGreaterThan(0)
    expect(screen.queryByText("1")).not.toBeInTheDocument()
    expect(screen.queryByText("0")).not.toBeInTheDocument()
  })

  it("allows a long log column to opt out of single-line truncation",()=>{
    render(<EntityTable rows={[{id:1,message:"第一行\n第二行很长的日志"}]} columns={[{key:"message",label:"消息",className:"max-w-xl whitespace-normal"}]}/>)
    const cells=screen.getAllByText(/第一行/)
    expect(cells.some(cell=>cell.closest("td")?.className.includes("whitespace-normal"))).toBe(true)
  })

  it("uses explicitly selected fields in mobile cards",()=>{
    const { container } = render(<EntityTable rows={[{id:1,username:"mobile@example.com",enable:true}]} columns={[
      {key:"username",label:"账号",mobile:true},
      {key:"recovery_email",label:"恢复邮箱"},
      {key:"enable",label:"启用",mobile:true,render:statusNode},
    ]}/>)
    const mobile = container.querySelector('[class~="md:hidden"]')
    expect(mobile).toHaveTextContent("账号")
    expect(mobile).toHaveTextContent("启用")
    expect(mobile).not.toHaveTextContent("恢复邮箱")
  })
})
