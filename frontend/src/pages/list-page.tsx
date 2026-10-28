import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { Copy, Eye } from "lucide-react"
import { RestartTask } from "@/components/restart-task"
import { toast } from "sonner"
import { api, getCsrfToken, post, withScope } from "@/lib/api"
import type { BootstrapData, Entity } from "@/types"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Switch } from "@/components/ui/switch"
import { EntityTable, statusNode, type Column } from "@/components/entity-table"
import { PerPageSelect, usePerPage } from "@/components/per-page-select"
import { AccountCredentials, AccountEditor, BatchShareEditor, DeleteAction, RedeemCodeGenerator, ShareEditor, ShareGroupManager, UserEditor } from "@/components/crud-editors"
import { ErrorState, PageSkeleton } from "@/components/page-state"
import { Pagination, PaginationContent, PaginationItem, PaginationNext, PaginationPrevious } from "@/components/ui/pagination"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog"

const configs: Record<string, { title: string; description: string; columns: Column[] }> = {
  accounts: { title: "Apple账号", description: "直接查看并维护 Apple 账号、恢复邮箱与自动化策略。", columns: [
    { key: "id", label: "ID", mobile: true }, { key: "username", label: "Apple 账号", mobile: true },
    { key: "password_configured", label: "密码", render: value => <Badge variant="secondary">{value ? "已配置" : "未配置"}</Badge> },
    { key: "recovery_email", label: "恢复邮箱" }, { key: "mailbox_username", label: "邮箱登录账号" },
    { key: "remark", label: "备注", mobile: true }, { key: "enable", label: "启用", mobile: true, render: statusNode },
    { key: "task_status", label: "任务状态", mobile: true, render: statusNode }, { key: "message", label: "最新消息", mobile: true },
    { key: "last_check", label: "上次检查" },
  ]},
  tasks: { title: "自动化任务", description: "查看任务状态、调度时间并重新启动异常任务。", columns: [
    { key: "id", label: "ID" }, { key: "username", label: "账号" }, { key: "enable", label: "启用", render: statusNode },
    { key: "task_status", label: "任务状态", render: statusNode }, { key: "message", label: "执行消息" },
    { key: "last_check", label: "上次同步" }, { key: "task_next_run_at", label: "下次运行" }, { key: "task_retry_count", label: "重试" },
  ]},
  records: { title: "执行记录", description: "审计自动化操作的结果、时间与异常信息，每个 Apple 账号仅保留最近 10 条。", columns: [
    { key: "id", label: "ID" }, { key: "username", label: "账号" },
    { key: "status", label: "结果", render: statusNode },
    { key: "message", label: "消息", className: "max-w-xl whitespace-normal", render: value => <span className="line-clamp-3 break-words whitespace-pre-wrap">{String(value || "—")}</span> },
    { key: "time", label: "执行时间" },
  ]},
  shares: { title: "分享页", description: "管理公开访问入口、有效期和关联账号。", columns: [
    { key: "id", label: "ID" }, { key: "remark", label: "备注" }, { key: "group_name", label: "分享组" }, { key: "share_link", label: "分享标识" },
    { key: "account_count", label: "账号数量" }, { key: "access_status", label: "访问状态", render: shareAccess },
    { key: "expiry_label", label: "有效期", render: shareExpiry },
  ]},
  orders: { title: "订单中心", description: "查询套餐订单、支付状态与历史记录。", columns: [
    { key: "order_no", label: "订单号" }, { key: "plan_name", label: "套餐" }, { key: "amount", label: "金额" },
    { key: "pay_type", label: "支付方式" }, { key: "status", label: "状态", render: orderStatus },
    { key: "paid_at", label: "支付时间" }, { key: "created_at", label: "创建时间" },
  ]},
  users: { title: "用户与权限", description: "管理平台用户、配额、有效期与管理权限。", columns: [
    { key: "username", label: "用户" }, { key: "is_admin", label: "管理员", render: statusNode },
    { key: "status", label: "状态", render: statusNode }, { key: "max_accounts", label: "账号配额" },
    { key: "max_share_pages", label: "分享配额" }, { key: "vip_expire_at", label: "服务有效期" },
  ]},
  "redeem-codes": { title: "兑换码", description: "生成并追踪套餐兑换码的使用状态。", columns: [
    { key: "code", label: "兑换码" }, { key: "plan_name", label: "套餐" }, { key: "status", label: "状态", render: statusNode },
    { key: "used_username", label: "使用用户" }, { key: "expire_at", label: "过期时间" }, { key: "used_at", label: "使用时间" },
  ]},
}

function shareAccess(_value: unknown, row: Entity) {
  const status = String(row.access_status ?? "")
  if (status === "used") return <Badge className="bg-teal-600 text-white dark:bg-teal-500">已访问</Badge>
  if (status === "unknown") return <Badge variant="secondary">历史未记录</Badge>
  return <Badge variant="secondary">未访问</Badge>
}

function shareExpiry(_value: unknown, row: Entity) {
  const expired = String(row.validity ?? "") === "expired"
  return <Badge variant={expired ? "destructive" : "default"} className={expired ? "" : "bg-teal-600 text-white dark:bg-teal-500"}>{String(row.expiry_label ?? "—")}</Badge>
}

function hasVipExpiry(user?: Entity | null) {
  if (user?.is_admin) return true
  const raw = user?.vip_expire_at
  if (!raw) return false
  const t = new Date(String(raw).replace(" ", "T")).getTime()
  return !Number.isNaN(t) && t > Date.now()
}

function orderStatus(_value: unknown, row: Entity) {
  const s = String(row.status ?? "")
  if (s === "paid") return <Badge className="bg-teal-600 text-white dark:bg-teal-500">已支付</Badge>
  if (s === "pending") return <Badge variant="secondary">待支付</Badge>
  if (s === "cancelled") return <Badge variant="destructive">已取消</Badge>
  return <Badge variant="secondary">{s || "—"}</Badge>
}

function continuePay(row: Entity) {
  const orderNo = String(row.order_no ?? "")
  if (!orderNo) return
  const form = document.createElement("form")
  form.method = "POST"
  form.action = `/pay/repay/${encodeURIComponent(orderNo)}`
  form.style.display = "none"
  const c = document.createElement("input"); c.name = "_token"; c.value = getCsrfToken(); form.appendChild(c)
  document.body.appendChild(form)
  form.submit()
}

function LogDetail({ row }: { row: Entity }) {
  const message = String(row.message || "—")
  const copy = async () => {
    try { await navigator.clipboard.writeText(message); toast.success("完整日志已复制") }
    catch { toast.error("复制失败，请手动选择日志内容") }
  }
  return <Dialog><DialogTrigger render={<Button size="sm" variant="outline"/>}><Eye/>查看详情</DialogTrigger><DialogContent className="max-h-[90vh] sm:max-w-3xl"><DialogHeader><DialogTitle>执行日志详情</DialogTitle><DialogDescription>记录 #{String(row.id ?? "—")} · {String(row.username ?? "未知账号")}</DialogDescription></DialogHeader><div className="grid gap-3 sm:grid-cols-3"><div><p className="text-xs text-muted-foreground">执行结果</p><div className="mt-1">{statusNode(row.status)}</div></div><div><p className="text-xs text-muted-foreground">执行时间</p><p className="mt-1 text-sm">{String(row.time ?? "—")}</p></div><div className="flex items-end justify-end"><Button size="sm" variant="outline" onClick={copy}><Copy/>复制完整日志</Button></div></div><pre className="max-h-[55vh] overflow-auto whitespace-pre-wrap break-words rounded-lg border bg-muted/40 p-4 font-sans text-sm leading-6">{message}</pre></DialogContent></Dialog>
}

export function ListPage({ kind, admin = false }: { kind: keyof typeof configs; admin?: boolean }) {
  const [page,setPage]=useState(1)
  const [perPage,setPerPage]=usePerPage(String(kind))
  const [selected,setSelected]=useState<Array<string|number>>([])
  const [searchInput,setSearchInput]=useState("")
  const [keyword,setKeyword]=useState("")
  const config = configs[kind]
  const endpoint = admin && ["users", "redeem-codes"].includes(kind) ? `admin/${kind}` : String(kind)
  useEffect(()=>{
    if(kind!=="shares")return
    const timer=window.setTimeout(()=>{setKeyword(searchInput.trim());setPage(1)},300)
    return()=>window.clearTimeout(timer)
  },[kind,searchInput])
  const searchParam=kind==="shares"&&keyword?`&keyword=${encodeURIComponent(keyword)}`:""
  const queryKey = [endpoint, admin, page, perPage, keyword]
  const query = useQuery({ queryKey, queryFn: () => api<Entity[]>(withScope(`${endpoint}?page=${page}&per_page=${perPage}${searchParam}`, admin)), placeholderData: previous=>previous, refetchInterval: ["accounts","tasks","records"].includes(kind) ? 10_000 : false })
  const boot = useQuery({ queryKey: ["bootstrap"], queryFn: () => api<BootstrapData>("bootstrap") })
  const subscribed = admin || hasVipExpiry(boot.data?.data?.user ?? null)
  const client = useQueryClient()
  const restart = useMutation({ mutationFn: (id: string | number) => post(`tasks/${id}/restart`, { mode: "due" }), onSuccess: r => { toast.success(r.message); client.invalidateQueries({ queryKey }) }, onError: (e: Error) => toast.error(e.message) })
  const remove = useMutation({mutationFn:({path,method="DELETE"}:{path:string;method?:string})=>api(path,{method}),onSuccess:r=>{toast.success(r.message);client.invalidateQueries({queryKey:[endpoint,admin]})},onError:(e:Error)=>toast.error(e.message)})
  const toggleEnable = useMutation({mutationFn:({id,enable}:{id:string|number;enable:boolean})=>post(withScope(`accounts/${id}/enable`,admin),{enable}),onSuccess:r=>{toast.success(r.message);client.invalidateQueries({queryKey:[endpoint,admin]})},onError:(e:Error)=>toast.error(e.message)})
  const toggleSelect = (id: string | number, checked: boolean) => setSelected(prev => checked ? [...prev.filter(x => x !== id), id] : prev.filter(x => x !== id))
  const batchDelete = useMutation({mutationFn:(ids:Array<string|number>)=>post(withScope("shares/batch-delete",admin),{ids}),onSuccess:r=>{toast.success(r.message);setSelected([]);client.invalidateQueries({queryKey:[endpoint,admin]})},onError:(e:Error)=>toast.error(e.message)})
  const pageRows=query.data?.data??[]
  const pageIds=kind==="shares"?pageRows.map(row=>row.id).filter((id):id is string|number=>id!==undefined):[]
  const allPageSelected=pageIds.length>0&&pageIds.every(id=>selected.includes(id))
  const togglePage=(checked:boolean)=>setSelected(prev=>checked?[...new Set([...prev,...pageIds])]:prev.filter(id=>!pageIds.includes(id)))
  const selectColumn: Column = { key: "__select", label: "选择", render: (_v: unknown, row: Entity) => <Checkbox aria-label="选择分享链接" checked={selected.includes(row.id!)} onCheckedChange={(checked) => toggleSelect(row.id!, Boolean(checked))} /> }
  const columns: Column[] = kind==="accounts"?config.columns.map(c=>c.key==="enable"?{...c,render:(_v:unknown,row:Entity)=><Switch aria-label={row.enable?"停用账号":"启用账号"} title={row.enable?"停用账号":"启用账号"} disabled={toggleEnable.isPending} checked={Boolean(row.enable)} onCheckedChange={(checked)=>toggleEnable.mutate({id:row.id!,enable:Boolean(checked)})}/>}:c):kind==="shares"?[selectColumn,...config.columns]:config.columns
  if (query.isLoading) return <PageSkeleton />
  if (query.error) return <ErrorState error={query.error} retry={() => query.refetch()} />
  const pages=query.data?.meta.total_pages??query.data?.meta.last_page??1
  const total=query.data?.meta.total??pageRows.length
  const changePerPage=(next:number)=>{setPerPage(next);setPage(1)}
  const refresh=()=>client.invalidateQueries({queryKey:[endpoint,admin]})
  const addAction=kind==="accounts"?<AccountEditor admin={admin} onSaved={refresh}/>:kind==="shares"?<ShareEditor admin={admin} onSaved={refresh}/>:kind==="users"?<UserEditor onSaved={refresh}/>:kind==="redeem-codes"?<RedeemCodeGenerator onSaved={refresh}/>:null
  const actions=(row:Entity)=>{if(kind==="records")return <LogDetail row={row}/>;if(kind==="tasks"){const pending=restart.isPending&&restart.variables===row.id;return <RestartTask disabled={restart.isPending&&!pending} pending={pending} onConfirm={()=>restart.mutate(row.id!)} />};if(kind==="accounts")return <div className="flex justify-end gap-1"><AccountCredentials row={row} admin={admin}/><AccountEditor row={row} admin={admin} onSaved={refresh}/><DeleteAction title="删除 Apple 账号？" description="账号、自动化任务及其分享关联会被移除。" onConfirm={()=>remove.mutate({path:`accounts/${row.id}${admin?"?scope=admin":""}`})}/></div>;if(kind==="shares")return <div className="flex justify-end gap-1"><ShareEditor row={row} admin={admin} onSaved={refresh}/><DeleteAction title="删除访问分享？" onConfirm={()=>remove.mutate({path:`shares/${row.id}${admin?"?scope=admin":""}`})}/></div>;if(kind==="users")return <div className="flex justify-end gap-1"><UserEditor row={row} onSaved={refresh}/><DeleteAction title="删除平台用户？" description="该用户所属账号、分享和代理也会被删除。" onConfirm={()=>remove.mutate({path:`admin/users/${row.id}`})}/></div>;if(kind==="orders")return String(row.status)==="pending"?<div className="flex justify-end gap-1"><Button size="sm" variant="outline" onClick={()=>continuePay(row)}>继续支付</Button><DeleteAction title="取消待支付订单？" description="取消后该订单不能继续支付。" onConfirm={()=>remove.mutate({path:`orders/${row.id}/cancel`,method:"POST"})}/></div>:null;return null}
  return <div className="space-y-5">{!admin&&(kind==="accounts"||kind==="shares")&&!subscribed&&<Alert className="border-dashed"><AlertTitle>需要订阅</AlertTitle><AlertDescription>{kind==="accounts"?"开通订阅后即可添加 Apple 账号":"开通订阅后即可创建分享页"}，请先在「订阅」页选购套餐。</AlertDescription></Alert>}<div className="flex flex-wrap items-end justify-between gap-3"><div><h2 className="text-2xl font-semibold tracking-tight">{kind==="orders"?(admin?"订单中心":"我的订单"):config.title}</h2><p className="mt-1 text-sm text-muted-foreground">{config.description}</p></div>{addAction}{kind==="shares"&&<div className="flex flex-wrap items-center gap-2"><ShareGroupManager admin={admin} onSaved={refresh}/><BatchShareEditor admin={admin} onSaved={refresh}/><label className="flex items-center gap-2 rounded-md border px-2.5 py-1 text-sm"><Checkbox aria-label="全选当前页分享链接" checked={allPageSelected} disabled={!pageIds.length} onCheckedChange={checked=>togglePage(Boolean(checked))}/>全选当前页</label><span className="text-sm text-muted-foreground">已选 {selected.length} 个</span><DeleteAction label="批量删除" disabled={!selected.length||batchDelete.isPending} title="删除选中的分享链接？" description={`将删除 ${selected.length} 个分享链接，且无法恢复。`} onConfirm={()=>batchDelete.mutate(selected)}/></div>}</div><EntityTable rows={query.data?.data ?? []} columns={columns} searchPlaceholder={kind==="shares"?"全局搜索分享标识、备注或分组":"搜索当前列表"} searchValue={kind==="shares"?searchInput:undefined} onSearchChange={kind==="shares"?setSearchInput:undefined} actions={["records","tasks","accounts","shares","users","orders"].includes(kind)?actions:undefined} /><div className="flex flex-wrap items-center justify-between gap-3"><p className="text-sm text-muted-foreground">共 {total} 条 · 本页 {pageRows.length} 条</p><PerPageSelect value={perPage} onChange={changePerPage} /></div>{pages>1&&<Pagination><PaginationContent><PaginationItem><PaginationPrevious href="#" text="上一页" aria-disabled={page<=1} onClick={e=>{e.preventDefault();if(page>1)setPage(page-1)}}/></PaginationItem><PaginationItem><span className="px-3 text-sm text-muted-foreground">{page} / {pages}</span></PaginationItem><PaginationItem><PaginationNext href="#" text="下一页" aria-disabled={page>=pages} onClick={e=>{e.preventDefault();if(page<pages)setPage(page+1)}}/></PaginationItem></PaginationContent></Pagination>}</div>
}
