import { useEffect } from "react"
import { useForm } from "react-hook-form"
import { useMutation, useQuery } from "@tanstack/react-query"
import { BellRing, LoaderCircle, Save } from "lucide-react"
import { toast } from "sonner"
import { api, post } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ErrorState, PageSkeleton } from "@/components/page-state"

type NotifyMode = "success" | "failure" | "all"
type Values = {
  tg_bot_token: string
  tg_chat_id: string
  tg_notify_mode: NotifyMode
  wx_pusher_id: string
  webhook: string
}

const defaults: Values = { tg_bot_token: "", tg_chat_id: "", tg_notify_mode: "all", wx_pusher_id: "", webhook: "" }

export function NotificationsPage() {
  const query = useQuery({ queryKey: ["notifications"], queryFn: () => api<Values>("notifications") })
  const form = useForm<Values>({ defaultValues: defaults })
  useEffect(() => { if (query.data?.data) form.reset({ ...defaults, ...query.data.data }) }, [query.data, form])
  useEffect(() => { const guard=(e:BeforeUnloadEvent)=>{if(form.formState.isDirty)e.preventDefault()}; addEventListener("beforeunload",guard); return()=>removeEventListener("beforeunload",guard)}, [form.formState.isDirty])
  const mutation = useMutation({
    mutationFn: (values: Values) => post("notifications", values),
    onSuccess: r => { toast.success(r.message); form.reset(form.getValues()) },
    onError: (e: Error) => toast.error(e.message),
  })
  if (query.isLoading) return <PageSkeleton />
  if (query.error) return <ErrorState error={query.error} retry={() => query.refetch()} />
  return <div className="max-w-3xl space-y-5">
    <div><h2 className="flex items-center gap-2 text-2xl font-semibold"><BellRing className="size-6"/>消息推送</h2><p className="mt-1 text-sm text-muted-foreground">配置任务结果通知。Telegram 验证码交互不受结果筛选影响。</p></div>
    <form onSubmit={form.handleSubmit(v=>mutation.mutate(v))}>
      <Card><CardHeader><CardTitle>Telegram</CardTitle><CardDescription>Token 与 Chat ID 必须同时配置；清空任意一项即可停止结果通知。</CardDescription></CardHeader><CardContent className="grid gap-5 sm:grid-cols-2">
        <div className="space-y-2"><Label htmlFor="tg_bot_token">Bot Token</Label><Input id="tg_bot_token" type="password" autoComplete="new-password" placeholder="Telegram Bot Token" {...form.register("tg_bot_token")}/></div>
        <div className="space-y-2"><Label htmlFor="tg_chat_id">Chat ID</Label><Input id="tg_chat_id" autoComplete="off" placeholder="Telegram Chat ID" {...form.register("tg_chat_id")}/></div>
        <div className="space-y-2 sm:col-span-2"><Label htmlFor="tg_notify_mode">任务结果推送</Label><select id="tg_notify_mode" className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50" {...form.register("tg_notify_mode")}><option value="all">成功和失败都推送</option><option value="success">仅推送成功消息</option><option value="failure">仅推送失败消息</option></select></div>
      </CardContent></Card>
      <Card className="mt-5"><CardHeader><CardTitle>其他通道</CardTitle><CardDescription>保留 PushPlus 与 Webhook 配置；当前版本暂未接入实际发送。</CardDescription></CardHeader><CardContent className="grid gap-5 sm:grid-cols-2">
        <div className="space-y-2"><Label htmlFor="wx_pusher_id">PushPlus Token</Label><Input id="wx_pusher_id" autoComplete="off" placeholder="可选" {...form.register("wx_pusher_id")}/></div>
        <div className="space-y-2"><Label htmlFor="webhook">Webhook 地址</Label><Input id="webhook" autoComplete="off" placeholder="https://..." {...form.register("webhook")}/></div>
        <div className="sm:col-span-2"><Button type="submit" disabled={mutation.isPending||!form.formState.isDirty}>{mutation.isPending?<LoaderCircle className="animate-spin"/>:<Save/>}保存推送设置</Button></div>
      </CardContent></Card>
    </form>
  </div>
}
