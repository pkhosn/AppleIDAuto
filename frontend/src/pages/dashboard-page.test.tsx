import "@testing-library/jest-dom/vitest"
import {render,screen,cleanup} from "@testing-library/react"
import {MemoryRouter} from "react-router-dom"
import {afterEach,describe,it,expect} from "vitest"
import {MembershipCard,type Membership} from "./dashboard-page"
afterEach(cleanup)
const member:Membership={plan_id:3,plan_name:"标准套餐",quota_custom:false,expires_at:"2030-01-01 00:00:00",remaining_days:3,status:"expiring",renewable:true,accounts:{used:2,limit:5,remaining:3,over_limit:false},shares:{used:8,limit:20,remaining:12,over_limit:false}}
describe("membership card",()=>{
 it("shows usage, expiry reminder and renewal confirmation link",()=>{
  render(<MemoryRouter><MembershipCard membership={member}/></MemoryRouter>)
  expect(screen.getByText("Apple账号 · 已用 2 / 5")).toBeVisible()
  expect(screen.getByText("剩余 12")).toBeVisible()
  expect(screen.getByText(/到期时间：2030/)).toBeVisible()
  expect(screen.getByRole("status")).toHaveTextContent("套餐将在 7 天内到期")
  expect(screen.getByRole("link",{name:"续费当前套餐"})).toHaveAttribute("href","/user/plan?renew=3")
 })
 it("distinguishes expired custom quotas and keeps excess usage visible",()=>{
  render(<MemoryRouter><MembershipCard membership={{...member,status:"expired",quota_custom:true,remaining_days:0,renewable:false,accounts:{used:8,limit:5,remaining:0,over_limit:true}}}/></MemoryRouter>)
  expect(screen.getByText("套餐已到期 · 自定义配额")).toBeVisible()
  expect(screen.getByText("已超额，暂停新增")).toBeVisible()
  expect(screen.queryByRole("link",{name:"续费当前套餐"})).not.toBeInTheDocument()
 })
 it("offers purchase without inventing a current plan",()=>{
  render(<MemoryRouter><MembershipCard membership={{...member,plan_id:null,plan_name:null,status:"none",expires_at:null,renewable:false}}/></MemoryRouter>)
  expect(screen.getByText("我的套餐 · 未购买")).toBeVisible()
  expect(screen.getByRole("link",{name:"购买套餐"})).toHaveAttribute("href","/user/plan")
 })
})
