import { useQuery } from "@tanstack/react-query"
import { Activity, Boxes, CircleAlert, Share2, UsersRound } from "lucide-react"
import type { LucideIcon } from "lucide-react"
import { api, withScope } from "@/lib/api"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { ErrorState, PageSkeleton } from "@/components/page-state"

type Dashboard = { accounts: number; shares: number; users: number | null; running_tasks: number; failed_tasks: number }

export function DashboardPage({ admin = false }: { admin?: boolean }) {
  const query = useQuery({ queryKey: ["dashboard", admin], queryFn: () => api<Dashboard>(withScope("dashboard", admin)) })
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
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{cards.map(([label, value, Icon, note]) => <Card key={label}><CardHeader className="flex-row items-center justify-between space-y-0 pb-2"><CardDescription>{label}</CardDescription><Icon className="size-4 text-muted-foreground" /></CardHeader><CardContent><p className="text-3xl font-semibold tabular-nums">{value}</p><p className="mt-1 text-xs text-muted-foreground">{note}</p></CardContent></Card>)}</div>
    <div className="grid gap-4 lg:grid-cols-2"><Card><CardHeader><CardTitle>任务健康度</CardTitle><CardDescription>运行中与异常任务的整体比例</CardDescription></CardHeader><CardContent className="space-y-4"><Progress value={totalTasks ? data.running_tasks / totalTasks * 100 : 100} /><div className="flex justify-between text-sm"><span className="text-muted-foreground">正常运行 {data.running_tasks}</span><span className={data.failed_tasks ? "text-destructive" : "text-muted-foreground"}>异常 {data.failed_tasks}</span></div></CardContent></Card><Card><CardHeader><CardTitle>安全建议</CardTitle><CardDescription>恢复链路和代理网络应保持双重可用</CardDescription></CardHeader><CardContent className="text-sm leading-6 text-muted-foreground">建议为账号同时配置邮件恢复与可靠代理。涉及密码、邮箱凭据的字段仅在保存时提交，接口不会回显邮箱密码。</CardContent></Card></div>
  </div>
}
