import { useEffect, useRef, useState } from "react"
import type { ReactNode } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { History, KeyRound, LoaderCircle, RotateCcw } from "lucide-react"
import { toast } from "sonner"
import { api, post, withScope } from "@/lib/api"
import type { Entity } from "@/types"
import { EntityTable, statusNode } from "@/components/entity-table"
import { ErrorState, PageSkeleton } from "@/components/page-state"
import { Button } from "@/components/ui/button"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

type RecoveryKind = "email-password" | "security-rollback"
type RecoveryRun = Entity & { run_id?: string; status?: string; stage?: string; error_code?: string; message?: string }

const recoveryErrors: Record<string, string> = {
  security_rollback_link_expired: "Apple 安全设置回退链接已失效，请使用 Apple 新发送的“恢复之前安全设置”邮件后再试。",
  recovery_email_timeout: "等待时间内未收到可验证的 Apple 邮件，账号未被修改。",
  account_not_frozen: "请先保存并禁用账号、自动改密和设备操作。",
  proxy_unavailable: "代理节点被要求但当前不可用；停用全部节点后将使用直连。",
  apple_authentication_challenge_required: "Apple 要求额外安全验证，请先在可信设备完成验证后重试。",
}

const actionLabels: Record<string, string> = {
  email_password_recovery: "邮件改密",
  security_rollback: "安全设置回退",
}

export function MailRecoveryPage() {
  const query = useQuery({
    queryKey: ["accounts", true, "mail"],
    queryFn: () => api<Entity[]>(withScope("accounts", true)),
  })
  if (query.isLoading) return <PageSkeleton />
  if (query.error) return <ErrorState error={query.error} retry={() => query.refetch()} />

  const rows = (query.data?.data ?? []).filter((row) => row.enable_email_recovery || row.mailbox_username || row.recovery_email)
  const methods = (_value: unknown, row: Entity) => {
    const labels: Record<string, string> = { account_login: "登录账号处理", email_password: "邮件修改密码", email_security: "邮件解除双重认证" }
    return (Array.isArray(row.recovery_methods) ? row.recovery_methods : []).map((value) => labels[String(value)] ?? String(value)).join("、") || "未配置"
  }

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-2xl font-semibold">邮件恢复</h2>
        <p className="mt-1 text-sm text-muted-foreground">集中审查恢复配置、发起受控邮件恢复并查看执行历史。账号必须先停用后才能发起操作。</p>
      </div>
      <EntityTable
        rows={rows}
        columns={[
          { key: "id", label: "ID" },
          { key: "username", label: "Apple 账号" },
          { key: "recovery_email", label: "恢复邮箱" },
          { key: "mailbox_username", label: "邮箱用户名" },
          { key: "mailbox_password_configured", label: "凭据状态", render: statusNode },
          { key: "recovery_methods", label: "允许方式", render: methods },
          { key: "last_recovery_status", label: "最近结果", render: statusNode },
        ]}
        actions={(row) => <RecoveryActions row={row} />}
      />
    </div>
  )
}

function RecoveryActions({ row }: { row: Entity }) {
  const client = useQueryClient()
  const timer = useRef<number | null>(null)
  const [runningKind, setRunningKind] = useState<RecoveryKind | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const accountId = Number(row.id ?? 0)
  const methods = Array.isArray(row.recovery_methods) ? row.recovery_methods.map(String) : []
  const frozen = !row.enable && !row.enable_auto_update_password && !row.enable_delete_devices

  useEffect(() => () => {
    if (timer.current !== null) window.clearTimeout(timer.current)
  }, [])

  const finish = (result: RecoveryRun) => {
    setRunningKind(null)
    void client.invalidateQueries({ queryKey: ["recovery-history", accountId] })
    void client.invalidateQueries({ queryKey: ["accounts", true, "mail"] })
    if (result.status === "success") {
      toast.success(result.message || "邮件恢复操作已完成，请在任务记录中复核")
      return
    }
    const code = String(result.error_code ?? "")
    const detail = recoveryErrors[code] || result.message || `阶段：${String(result.stage ?? "未知")}；原因：${code || "未知"}`
    toast.error(detail)
  }

  const poll = async (runId: string, attempt: number) => {
    try {
      const response = await api<RecoveryRun>(`admin/recoveries/${encodeURIComponent(runId)}`)
      if (response.data?.status === "running" && attempt < 120) {
        timer.current = window.setTimeout(() => void poll(runId, attempt + 1), 3000)
        return
      }
      if (response.data?.status === "running") {
        setRunningKind(null)
        toast.info("恢复仍在执行，请稍后在任务记录或恢复历史中复核")
        return
      }
      finish(response.data ?? {})
    } catch (error) {
      setRunningKind(null)
      toast.error(error instanceof Error ? error.message : "无法读取恢复状态，任务可能仍在执行")
    }
  }

  const start = async (kind: RecoveryKind) => {
    setRunningKind(kind)
    try {
      const response = await post<RecoveryRun>(`admin/accounts/${accountId}/recovery/${kind}`)
      const runId = String(response.data?.run_id ?? "")
      if (!runId) throw new Error("恢复服务未返回任务标识")
      toast.info("恢复任务已提交，正在等待受控任务完成")
      // Count the immediate status read as the first of at most 120 polls.
      await poll(runId, 1)
    } catch (error) {
      setRunningKind(null)
      toast.error(error instanceof Error ? error.message : "无法提交恢复任务")
    }
  }

  const disabledReason = !frozen ? "请先停用账号、自动改密和设备操作" : ""

  return (
    <div className="flex flex-wrap justify-end gap-1">
      <RecoveryConfirm
        title="通过邮件重设密码？"
        description="将使用已配置的救援邮箱发起一次 Apple 密码重置。账号必须保持停用，提交后请勿重复操作。"
        disabled={runningKind !== null || !frozen || !methods.includes("email_password")}
        disabledReason={disabledReason || (!methods.includes("email_password") ? "该账号未启用邮件修改密码" : "")}
        onConfirm={() => void start("email-password")}
      >
        {runningKind === "email-password" ? <LoaderCircle className="animate-spin" /> : <KeyRound />}
        邮件改密
      </RecoveryConfirm>
      <RecoveryConfirm
        title="恢复原安全设置？"
        description="将使用近期 Apple 双重认证确认邮件中的官方链接恢复原安全设置。链接为一次性操作，请确认后继续。"
        disabled={runningKind !== null || !frozen || !methods.includes("email_security")}
        disabledReason={disabledReason || (!methods.includes("email_security") ? "该账号未启用邮件解除双重认证" : "")}
        onConfirm={() => void start("security-rollback")}
      >
        {runningKind === "security-rollback" ? <LoaderCircle className="animate-spin" /> : <RotateCcw />}
        安全回退
      </RecoveryConfirm>
      <Button size="sm" variant="ghost" onClick={() => setHistoryOpen(true)}><History />历史</Button>
      <RecoveryHistory accountId={accountId} username={String(row.username ?? accountId)} open={historyOpen} onOpenChange={setHistoryOpen} />
    </div>
  )
}

function RecoveryConfirm({ title, description, disabled, disabledReason, onConfirm, children }: {
  title: string
  description: string
  disabled: boolean
  disabledReason: string
  onConfirm: () => void
  children: ReactNode
}) {
  const [open, setOpen] = useState(false)
  return (
    <AlertDialog open={open} onOpenChange={setOpen}>
      <AlertDialogTrigger render={<Button size="sm" variant="outline" disabled={disabled} title={disabled ? disabledReason : undefined} />}>
        {children}
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <AlertDialogAction onClick={() => { setOpen(false); onConfirm() }}>确认提交</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

function RecoveryHistory({ accountId, username, open, onOpenChange }: { accountId: number; username: string; open: boolean; onOpenChange: (open: boolean) => void }) {
  const query = useQuery({
    queryKey: ["recovery-history", accountId],
    queryFn: () => api<RecoveryRun[]>(`admin/accounts/${accountId}/recovery-history`),
    enabled: open,
  })
  const rows = query.data?.data ?? []
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>邮件恢复历史</DialogTitle>
          <DialogDescription>{username} 的受控邮件恢复执行记录。</DialogDescription>
        </DialogHeader>
        {query.isLoading ? <PageSkeleton /> : query.error ? (
          <ErrorState error={query.error} retry={() => query.refetch()} />
        ) : (
          <EntityTable
            hideSearch
            rows={rows}
            columns={[
              { key: "action", label: "操作", render: (value) => actionLabels[String(value)] ?? String(value ?? "未知操作") },
              { key: "status", label: "状态", render: statusNode },
              { key: "message", label: "详情", className: "max-w-md whitespace-normal" },
              { key: "created_at", label: "时间" },
            ]}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}
