import { Link } from "react-router-dom"
import { buttonVariants } from "@/components/ui/button"
import { useQuery } from "@tanstack/react-query"
import { Activity, Boxes, CircleAlert, Share2, UsersRound } from "lucide-react"
import type { LucideIcon } from "lucide-react"
import { api, withScope } from "@/lib/api"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { ErrorState, PageSkeleton } from "@/components/page-state"

type Quota = {used:number;limit:number;remaining:number;over_limit:boolean}
export type Membership = {plan_id:number|null;plan_name:string|null;quota_custom:boolean;expires_at:string|null;remaining_days:number;status:"none"|"expired"|"expiring"|"active";renewable:boolean;accounts:Quota;shares:Quota}

type Dashboard = { accounts: number; shares: number; users: number | null; running_tasks: number; failed_tasks: number; membership?: Membership | null }

export function DashboardPage({ admin = false }: { admin?: boolean }) {
  const query = useQuery({ queryKey: ["dashboard", admin], queryFn: () => api<Dashboard>(withScope("dashboard", admin)), refetchInterval: 60_000 })
  if (query.isLoading) return <PageSkeleton />
  if (query.error) return <ErrorState error={query.error} retry={() => query.refetch()} />
  const data = query.data!.data
  const cards: Array<readonly [string, number, LucideIcon, string]> = [
    ["Apple账号", data.accounts, Boxes, "已纳入统一管理"], ["分享页", data.shares, Share2, "当前分享页面"],
    ["执行中任务", data.running_tasks, Activity, "自动化队列实时状态"], ["异常任务", data.failed_tasks, CircleAlert, "建议及时处理"],
  ]
  if (admin) cards.push(["平台用户", data.users ?? 0, UsersRound, "拥有访问权限的用户"])
  const totalTasks = data.running_tasks + data.failed_tasks
  return <div className="space-y-6">
    <div><h2 className="text-2xl font-semibold tracking-tight">{admin ? "管理概览" : "今天的运行情况"}</h2><p className="mt-1 text-sm text-muted-foreground">关键业务状态和近期需要关注的事项。</p></div>
    {!admin&&data.membership&&<MembershipCard membership={data.membership}/>}
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{cards.map(([label, value, Icon, note]) => <Card key={label}><CardHeader className="flex-row items-center justify-between space-y-0 pb-2"><CardDescription>{label}</CardDescription><Icon className="size-4 text-muted-foreground" /></CardHeader><CardContent><p className="text-3xl font-semibold tabular-nums">{value}</p><p className="mt-1 text-xs text-muted-foreground">{note}</p></CardContent></Card>)}</div>
    <div className="grid gap-4 lg:grid-cols-2"><Card><CardHeader><CardTitle>任务健康度</CardTitle><CardDescription>运行中与异常任务的整体比例</CardDescription></CardHeader><CardContent className="space-y-4"><Progress value={totalTasks ? data.running_tasks / totalTasks * 100 : 100} /><div className="flex justify-between text-sm"><span className="text-muted-foreground">正常运行 {data.running_tasks}</span><span className={data.failed_tasks ? "text-destructive" : "text-muted-foreground"}>异常 {data.failed_tasks}</span></div></CardContent></Card><Card><CardHeader><CardTitle>安全建议</CardTitle><CardDescription>恢复链路和代理网络应保持双重可用</CardDescription></CardHeader><CardContent className="text-sm leading-6 text-muted-foreground">建议为账号同时配置邮件恢复与可靠代理。涉及密码、邮箱凭据的字段仅在保存时提交，接口不会回显邮箱密码。</CardContent></Card></div>
  </div>
}


export function MembershipCard({membership:m}:{membership:Membership}) {
  const labels={none:"尚未购买套餐",expired:"套餐已到期",expiring:"即将到期",active:"使用中"}
  return <Card><CardHeader><div className="flex flex-wrap items-start justify-between gap-3"><div className="space-y-1"><CardTitle>我的套餐 · {m.plan_name??(m.status==="none"?"未购买":"未关联套餐")}</CardTitle><CardDescription>{labels[m.status]}{m.quota_custom?" · 自定义配额":" · 配额跟随套餐"}</CardDescription></div><div className="flex gap-2">{m.renewable&&<Link className={buttonVariants({size:"sm"})} to={`/user/plan?renew=${m.plan_id}`}>续费当前套餐</Link>}<Link className={buttonVariants({size:"sm",variant:"outline"})} to="/user/plan">购买套餐</Link></div></div></CardHeader><CardContent className="space-y-4"><div className="grid gap-4 sm:grid-cols-2">{([["Apple账号",m.accounts],["分享页",m.shares]] as const).map(([name,q])=><div key={name} className="space-y-2"><p className="flex justify-between text-sm"><span>{name} · 已用 {q.used} / {q.limit}</span><span className={q.over_limit?"text-destructive":"text-muted-foreground"}>{q.over_limit?"已超额，暂停新增":`剩余 ${q.remaining}`}</span></p><Progress value={q.limit>0?Math.min(100,q.used/q.limit*100):q.used>0?100:0}/></div>)}</div><p className="text-sm text-muted-foreground">{m.expires_at?`到期时间：${m.expires_at} · 剩余 ${m.remaining_days} 天`:"购买套餐后即可使用账号与分享额度。"}</p>{(m.status==="expiring"||m.status==="expired")&&<p role="status" className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm">{m.status==="expired"?"套餐已到期，请续费后继续使用。":"套餐将在 7 天内到期，请及时续费；提前续费会从原到期时间顺延。"}</p>}</CardContent></Card>
}
