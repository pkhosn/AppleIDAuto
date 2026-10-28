import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Check, Coins, Crown, Pencil, Plus, Wallet } from "lucide-react"
import { toast } from "sonner"
import { api, getCsrfToken, post, withScope } from "@/lib/api"
import type { BootstrapData, Entity } from "@/types"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { EntityTable, statusNode } from "@/components/entity-table"
import { DeleteAction } from "@/components/crud-editors"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Pagination, PaginationContent, PaginationItem, PaginationNext, PaginationPrevious } from "@/components/ui/pagination"
import { PerPageSelect, usePerPage } from "@/components/per-page-select"
import { ErrorState, PageSkeleton } from "@/components/page-state"

const RECHARGE_PRESETS = [10, 50, 100, 200]

export function PlansPage({ admin = false }: { admin?: boolean }) {
  const client = useQueryClient()
  const [planPage, setPlanPage] = useState(1)
  const [planPerPage, setPlanPerPage] = usePerPage("plans")
  const query = useQuery({ queryKey: ["plans", admin, planPage, planPerPage], queryFn: () => api<Entity[]>(withScope(`plans?page=${planPage}&per_page=${planPerPage}`, admin)) })
  const boot = useQuery({ queryKey: ["bootstrap"], queryFn: () => api<BootstrapData>("bootstrap") })
  const [rechargeOpen, setRechargeOpen] = useState(false)
  const [buyPlan, setBuyPlan] = useState<Entity | null>(null)
  const buy = useMutation({
    mutationFn: ({ planId }: { planId: string | number }) => post(`plans/${planId}/balance-buy`),
    onSuccess: (r) => { toast.success(r.message); client.invalidateQueries({ queryKey: ["bootstrap"] }) },
    onError: (e: Error) => toast.error(e.message),
  })
  if (query.isLoading) return <PageSkeleton />
  if (query.error) return <ErrorState error={query.error} retry={() => query.refetch()} />
  const rows = query.data?.data ?? []
  const planPages = query.data?.meta?.total_pages ?? query.data?.meta?.last_page ?? 1
  const changePlanPerPage = (next: number) => { setPlanPerPage(next); setPlanPage(1) }
  const refresh = () => client.invalidateQueries({ queryKey: ["plans", admin] })
  if (admin) return <div className="space-y-5"><div className="flex items-end justify-between gap-3"><div><h2 className="text-2xl font-semibold">套餐配置</h2><p className="mt-1 text-sm text-muted-foreground">维护平台可售套餐、配额和有效期。</p></div><PlanEditor onSaved={refresh} /></div><EntityTable rows={rows} columns={[{ key: "id", label: "ID" }, { key: "name", label: "套餐" }, { key: "price", label: "价格" }, { key: "days", label: "有效期(天)" }, { key: "max_accounts", label: "账号上限" }, { key: "max_share_pages", label: "分享上限" }, { key: "status", label: "状态", render: statusNode }]} actions={row => <PlanActions row={row} onSaved={refresh} />} /><div className="flex flex-wrap items-center justify-between gap-3"><p className="text-sm text-muted-foreground">共 {query.data?.meta?.total ?? rows.length} 条</p><PerPageSelect value={planPerPage} onChange={changePlanPerPage} /></div>{planPages > 1 && <Pagination><PaginationContent><PaginationItem><PaginationPrevious href="#" text="上一页" aria-disabled={planPage <= 1} onClick={e => { e.preventDefault(); if (planPage > 1) setPlanPage(planPage - 1) }} /></PaginationItem><PaginationItem><span className="px-3 text-sm text-muted-foreground">{planPage} / {planPages}</span></PaginationItem><PaginationItem><PaginationNext href="#" text="下一页" aria-disabled={planPage >= planPages} onClick={e => { e.preventDefault(); if (planPage < planPages) setPlanPage(planPage + 1) }} /></PaginationItem></PaginationContent></Pagination>}</div>

  const balance = Number(boot.data?.data?.user?.balance ?? 0)
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h2 className="text-2xl font-semibold">订阅</h2><p className="mt-1 text-sm text-muted-foreground">选择适合当前业务规模的服务配额。</p></div>
        <Button variant="outline" onClick={() => setRechargeOpen(true)}><Wallet className="size-4" />余额 ￥{balance.toFixed(2)} · 充值</Button>
      </div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {rows.map((plan, index) => {
          const price = Number(plan.price ?? 0)
          const affordable = balance >= price && price > 0
          return (
            <Card key={String(plan.id)} className={index === 1 ? "ring-2 ring-primary" : ""}>
              <CardHeader>
                <div className="mb-2 flex size-9 items-center justify-center rounded-lg bg-primary/10 text-primary"><Crown className="size-4" /></div>
                <CardTitle>{String(plan.name ?? "标准套餐")}</CardTitle>
                <CardDescription><span className="text-3xl font-semibold text-foreground">￥{String(plan.price ?? "0.00")}</span> / {String(plan.days ?? 0)} 天</CardDescription>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <p className="flex gap-2"><Check className="size-4 text-teal-600" />账号上限 {String(plan.max_accounts ?? "—")}</p>
                <p className="flex gap-2"><Check className="size-4 text-teal-600" />分享页上限 {String(plan.max_share_pages ?? "—")}</p>
                <p className="flex gap-2"><Check className="size-4 text-teal-600" />手动间隔 {String(plan.min_manual_interval ?? 0)} 分钟</p>
              </CardContent>
              <CardFooter className="flex-col items-stretch gap-2">
                <Button className="w-full" onClick={() => setBuyPlan(plan)}><Crown className="size-4" />立即购买</Button>
                {price > 0 ? (
                  affordable
                    ? <Button variant="outline" className="w-full" disabled={buy.isPending} onClick={() => buy.mutate({ planId: plan.id! })}><Coins className="size-4" />余额支付 ￥{price.toFixed(2)}</Button>
                    : <Button variant="ghost" className="w-full" onClick={() => setRechargeOpen(true)}>余额不足（￥{balance.toFixed(2)}），去充值</Button>
                ) : null}
              </CardFooter>
            </Card>
          )
        })}
      </div>
      <BuyDialog plan={buyPlan} onClose={() => setBuyPlan(null)} />
      <RechargeDialog open={rechargeOpen} onOpenChange={setRechargeOpen} />
    </div>
  )
}

function BuyDialog({ plan, onClose }: { plan: Entity | null; onClose: () => void }) {
  const [payType, setPayType] = useState("alipay")
  useEffect(() => { if (!plan) setPayType("alipay") }, [plan])
  const goPay = () => {
    if (!plan) return
    const form = document.createElement("form")
    form.method = "POST"
    form.action = "/pay/create"
    form.style.display = "none"
    const p = document.createElement("input"); p.name = "plan_id"; p.value = String(plan.id); form.appendChild(p)
    const t = document.createElement("input"); t.name = "type"; t.value = payType; form.appendChild(t)
    const c = document.createElement("input"); c.name = "_token"; c.value = getCsrfToken(); form.appendChild(c)
    document.body.appendChild(form); form.submit()
  }
  const price = plan ? Number(plan.price ?? 0) : 0
  return (
    <Dialog open={!!plan} onOpenChange={open => { if (!open) onClose() }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader><DialogTitle>确认购买订阅</DialogTitle><DialogDescription>支付成功后套餐配额即时生效，可在订单中查看记录。</DialogDescription></DialogHeader>
        {plan && <div className="space-y-4">
          <div className="flex items-center justify-between rounded-lg border p-3"><span className="text-sm">{String(plan.name ?? "")}</span><span className="font-semibold">￥{price.toFixed(2)}</span></div>
          <div className="space-y-2"><Label>支付方式</Label><div className="flex gap-2">{[["alipay", "支付宝"], ["wxpay", "微信支付"]].map(([v, label]) => <Button key={v} size="sm" variant={payType === v ? "default" : "outline"} onClick={() => setPayType(v)}>{label}</Button>)}</div></div>
        </div>}
        <DialogFooter><Button variant="outline" onClick={onClose}>取消</Button><Button onClick={goPay} disabled={!plan}>去支付 ￥{price.toFixed(2)}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function RechargeDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const [amount, setAmount] = useState(50)
  const [payType, setPayType] = useState("alipay")
  useEffect(() => { if (!open) { setAmount(50); setPayType("alipay") } }, [open])
  const goRecharge = () => {
    const value = Number(amount)
    if (!Number.isFinite(value) || value < 1 || value > 100000) { toast.error("请输入 1~100000 之间的充值金额"); return }
    const form = document.createElement("form")
    form.method = "POST"
    form.action = "/pay/recharge"
    form.style.display = "none"
    const a = document.createElement("input"); a.name = "amount"; a.value = String(value); form.appendChild(a)
    const t = document.createElement("input"); t.name = "type"; t.value = payType; form.appendChild(t)
    const c = document.createElement("input"); c.name = "_token"; c.value = getCsrfToken(); form.appendChild(c)
    document.body.appendChild(form); form.submit()
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader><DialogTitle>余额充值</DialogTitle><DialogDescription>选择金额后跳转在线支付，到账后可用于购买订阅。</DialogDescription></DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2"><Label>快捷金额</Label><div className="flex flex-wrap gap-2">{RECHARGE_PRESETS.map(p => <Button key={p} size="sm" variant={amount === p ? "default" : "outline"} onClick={() => setAmount(p)}>￥{p}</Button>)}</div></div>
          <div className="space-y-2"><Label htmlFor="recharge-amount">自定义金额</Label><Input id="recharge-amount" type="number" min={1} value={String(amount)} onChange={(e) => setAmount(Number(e.target.value))} /></div>
          <div className="space-y-2"><Label>支付方式</Label><div className="flex gap-2">{[["alipay", "支付宝"], ["wxpay", "微信支付"]].map(([v, label]) => <Button key={v} size="sm" variant={payType === v ? "default" : "outline"} onClick={() => setPayType(v)}>{label}</Button>)}</div></div>
        </div>
        <DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>取消</Button><Button onClick={goRecharge}>去充值 ￥{Number(amount).toFixed(2)}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

type PlanValues = { name: string; price: string; days: number; max_accounts: number; min_manual_interval: number; enable_share: boolean; max_share_pages: number; status: boolean }
const planDefaults: PlanValues = { name: "", price: "0.00", days: 30, max_accounts: 5, min_manual_interval: 0, enable_share: true, max_share_pages: 1, status: true }
function PlanEditor({ row, onSaved }: { row?: Entity; onSaved: () => void }) {
  const [open, setOpen] = useState(false)
  const [values, setValues] = useState(planDefaults)
  useEffect(() => {
    if (open) setValues({ ...planDefaults, ...row, name: String(row?.name ?? ""), price: String(row?.price ?? "0.00"), days: Number(row?.days ?? 30), max_accounts: Number(row?.max_accounts ?? 5), min_manual_interval: Number(row?.min_manual_interval ?? 0), enable_share: Boolean(row?.enable_share ?? true), max_share_pages: Number(row?.max_share_pages ?? 1), status: Boolean(row?.status ?? true) })
  }, [open, row])
  const mutation = useMutation({ mutationFn: () => post(`admin/plans${row?.id ? `/${row.id}` : ""}`, values), onSuccess: r => { toast.success(r.message); setOpen(false); onSaved() }, onError: (e: Error) => toast.error(e.message) })
  const field = (key: keyof PlanValues, label: string, type = "number") => <div className="space-y-2"><Label>{label}</Label><Input type={type} value={String(values[key])} onChange={e => setValues(v => ({ ...v, [key]: type === "number" ? Number(e.target.value) : e.target.value }))} /></div>
  return (
    <>
      <Button size="sm" variant={row ? "ghost" : "default"} onClick={() => setOpen(true)}>{row ? <Pencil /> : <Plus />}{row ? "编辑" : "添加套餐"}</Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader><DialogTitle>{row ? "编辑套餐" : "添加套餐"}</DialogTitle><DialogDescription>套餐配额在付款或兑换成功后发放给用户。</DialogDescription></DialogHeader>
          <div className="grid gap-4 sm:grid-cols-2">
            {field("name", "套餐名称", "text")}{field("price", "价格")}{field("days", "有效期（天）")}{field("max_accounts", "账号上限")}{field("min_manual_interval", "手动间隔（分钟）")}{field("max_share_pages", "分享页上限")}
            <div className="flex items-center justify-between rounded-lg border p-3"><Label>启用分享</Label><Switch checked={values.enable_share} onCheckedChange={enable_share => setValues(v => ({ ...v, enable_share }))} /></div>
            <div className="flex items-center justify-between rounded-lg border p-3"><Label>上架套餐</Label><Switch checked={values.status} onCheckedChange={status => setValues(v => ({ ...v, status }))} /></div>
          </div>
          <DialogFooter><Button variant="outline" onClick={() => setOpen(false)}>取消</Button><Button disabled={mutation.isPending || !values.name} onClick={() => mutation.mutate()}>保存套餐</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

function PlanActions({ row, onSaved }: { row: Entity; onSaved: () => void }) {
  const mutation = useMutation({ mutationFn: () => api(`admin/plans/${row.id}`, { method: "DELETE" }), onSuccess: r => { toast.success(r.message); onSaved() }, onError: (e: Error) => toast.error(e.message) })
  return <div className="flex justify-end gap-1"><PlanEditor row={row} onSaved={onSaved} /><DeleteAction title="删除或停用套餐？" description="已有订单的套餐会被安全地改为停用。" onConfirm={() => mutation.mutate()} /></div>
}
