import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { AlertCircle, ZoomIn, ZoomOut } from "lucide-react"
import { api, withScope } from "@/lib/api"
import type { Entity } from "@/types"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog"

type FailureImage = { name: string; stage: string; captured_at: string }
type FailureDetails = {
  message: string; failed_at: string; stage?: string; error_code?: string
  evidence_at?: string; warning?: string; images: FailureImage[]
}

function displayTime(value?: string) {
  if (!value) return "未记录"
  if (!value.includes("T")) return value
  const time = new Date(value)
  return Number.isNaN(time.getTime()) ? value : time.toLocaleString("zh-CN", { hour12: false })
}

function displayStage(stage: string) {
  const labels: Record<string, string> = {
    login: "登录认证", check: "账号检测", password_form: "密码表单", password_submitted: "密码提交",
    complete_email_reset: "邮件重设密码", complete_security_rollback: "恢复安全设置",
    request_email_reset: "请求重置邮件", delete_devices: "移除设备", update_password: "更新密码",
    interrupted: "任务中断", workflow: "自动化流程",
  }
  const attempt = /^attempt-(\d+)$/.exec(stage)
  return labels[stage] || (attempt ? `第 ${attempt[1]} 次尝试` : stage) || "失败现场"
}

function Screenshot({ src, label }: { src: string; label: string }) {
  const [failed, setFailed] = useState(false)
  const [zoomed, setZoomed] = useState(false)
  if (failed) return <p role="status" className="rounded-lg border border-dashed p-8 text-center text-muted-foreground">截图已清理或暂时无法读取，失败原因仍可在上方查看。</p>
  return <div className="min-w-0 max-w-full space-y-2">
    <Button size="sm" variant="outline" onClick={() => setZoomed(!zoomed)}>
      {zoomed ? <ZoomOut/> : <ZoomIn/>}{zoomed ? "适应窗口" : "放大截图"}
    </Button>
    <div className="max-h-[50vh] w-full min-w-0 overflow-auto rounded-lg border bg-muted/30">
      <button type="button" className={zoomed ? "block min-w-full cursor-zoom-out" : "block w-full cursor-zoom-in"}
        aria-label={zoomed ? "缩小失败截图" : "放大失败截图"} onClick={() => setZoomed(!zoomed)}>
        <img src={src} alt={label} onError={() => setFailed(true)} className={zoomed ? "mx-auto max-w-none" : "mx-auto h-auto max-w-full"}/>
      </button>
    </div>
  </div>
}

export function AccountFailure({ row, admin }: { row: Entity; admin: boolean }) {
  const [open, setOpen] = useState(false)
  const [selected, setSelected] = useState("")
  const isFailed = String(row.task_status) === "failed"
  const details = useQuery({
    queryKey: ["account-failure", row.id, admin, row.last_check, row.message],
    queryFn: () => api<FailureDetails>(withScope(`accounts/${row.id}/failure`, admin)),
    enabled: open && isFailed, staleTime: 0,
  })
  if (!isFailed) return <span className="text-muted-foreground">—</span>
  const data = details.data?.data
  const images = data?.images ?? []
  const current = images.find(image => image.name === selected) ?? images[0]
  const imageUrl = current ? `/frontend-api/v1/${withScope(`accounts/${row.id}/failure-image?name=${encodeURIComponent(current.name)}`, admin)}` : ""
  return <Dialog open={open} onOpenChange={value => { setOpen(value); if (value) setSelected("") }}>
    <DialogTrigger render={<Button size="sm" variant="outline"/>}><AlertCircle/>失败原因</DialogTrigger>
    <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-4xl">
      <DialogHeader>
        <DialogTitle>任务失败详情</DialogTitle>
        <DialogDescription className="break-all">{String(row.username)} · 账号 #{String(row.id)}</DialogDescription>
      </DialogHeader>
      <div className="min-w-0 space-y-4">
        <div className="rounded-lg border bg-muted/30 p-3">
          <p className="whitespace-pre-wrap break-words text-sm">{data?.message || String(row.message || "暂无失败说明")}</p>
          <p className="mt-2 text-xs text-muted-foreground">最近失败时间：{displayTime(data?.failed_at || String(row.last_check || ""))}</p>
          {data?.error_code && <p className="mt-1 break-words text-xs text-muted-foreground">截图对应错误：{data.error_code}{data.stage ? ` · 阶段：${displayStage(data.stage)}` : ""}</p>}
        </div>
        {details.isPending ? <p role="status">正在加载失败截图…</p> : details.isError ? <div role="alert">
          <p className="text-destructive">{details.error.message}</p>
          <Button size="sm" variant="outline" className="mt-2" onClick={() => details.refetch()}>重新加载</Button>
        </div> : <>
          {data?.warning && <p role="status" className="text-sm text-muted-foreground">{data.warning}</p>}
          {current ? <>
            <label className="flex flex-wrap items-center gap-2 text-sm">失败截图
              <select aria-label="选择失败截图" className="min-w-0 max-w-full rounded-lg border bg-background px-3 py-2"
                value={current.name} onChange={event => setSelected(event.target.value)}>
                {images.map((image, index) => <option key={image.name} value={image.name}>{index + 1}. {displayStage(image.stage)}</option>)}
              </select>
            </label>
            <p className="text-xs text-muted-foreground">截图采集时间：{displayTime(current.captured_at)}。截图展示对应任务的页面，可能早于最近失败时间。</p>
            <Screenshot key={imageUrl} src={imageUrl} label={`失败截图：${displayStage(current.stage)}`}/>
          </> : <p role="status" className="rounded-lg border border-dashed p-8 text-center text-muted-foreground">暂无可用截图：本次未生成截图、历史记录未关联截图，或截图已被清理。</p>}
        </>}
      </div>
    </DialogContent>
  </Dialog>
}
