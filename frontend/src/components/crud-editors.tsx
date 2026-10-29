import { useEffect, useId, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Copy, Eye, EyeOff, Pencil, Plus, Trash2 } from "lucide-react"
import { toast } from "sonner"
import { api, post, withScope } from "@/lib/api"
import type { BootstrapData, Entity } from "@/types"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from "@/components/ui/alert-dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Switch } from "@/components/ui/switch"
import { AccountCycles, defaultCycles, defaultSharedCycle, validCycles, validSharedCycle, type Cycles, type SharedCycle } from "@/components/automation-cycles"

export function DeleteAction({ title, description = "删除后无法恢复。", onConfirm, label, disabled = false }: { title: string; description?: string; onConfirm: () => void; label?: string; disabled?: boolean }) {
  return (
    <AlertDialog>
      <AlertDialogTrigger render={<Button size="sm" variant={label ? "destructive" : "ghost"} disabled={disabled} />}>
        <Trash2 />
        {label ?? <span className="sr-only">删除</span>}
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={onConfirm}>确认删除</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

type AccountValues = {
  automation_schedule: Cycles
  shared_cycle: SharedCycle
  username: string
  password: string
  dob: string
  question1: string
  answer1: string
  question2: string
  answer2: string
  question3: string
  answer3: string
  remark: string
  frontend_remark: string
  owner: number
  proxy_id: number
  check_interval: number
  min_manual_unlock: number
  enable_check_password_correct: boolean
  enable: boolean
  recovery_methods: string[]
  recovery_email: string
  mailbox_host: string
  mailbox_port: number
  mailbox_username: string
  mailbox_password: string
  mailbox_tls: boolean
  enable_auto_update_password: boolean
  enable_auto_disable_two_factor: boolean
  enable_auto_restore_profile: boolean
  enable_delete_devices: boolean
  profile_family_name: string
  profile_middle_name: string
  profile_given_name: string
  profile_country: string
  profile_language: string
  profile_observed: Record<string, unknown>
}

const accountDefaults: AccountValues = {
  automation_schedule: defaultCycles,
  shared_cycle: defaultSharedCycle,
  username: "",
  password: "",
  dob: "2000-01-01",
  question1: "",
  answer1: "",
  question2: "",
  answer2: "",
  question3: "",
  answer3: "",
  remark: "",
  frontend_remark: "",
  owner: 0,
  proxy_id: 0,
  check_interval: 30,
  min_manual_unlock: 0,
  enable_check_password_correct: true,
  enable: true,
  recovery_methods: ["email_password", "email_security"],
  recovery_email: "",
  mailbox_host: "",
  mailbox_port: 993,
  mailbox_username: "",
  mailbox_password: "",
  mailbox_tls: true,
  enable_auto_update_password: false,
  enable_auto_disable_two_factor: false,
  enable_auto_restore_profile: false,
  enable_delete_devices: false,
  profile_family_name: "",
  profile_middle_name: "",
  profile_given_name: "",
  profile_country: "",
  profile_language: "",
  profile_observed: {},
}

const EMAIL_RECOVERY_METHODS = ["email_password", "email_security"]

export function AccountEditor({ row, admin, onSaved }: { row?: Entity; admin: boolean; onSaved: () => void }) {
  const [open, setOpen] = useState(false)
  const [values, setValues] = useState<AccountValues>(accountDefaults)
  const [loading, setLoading] = useState(false)
  const boot = useQuery({ queryKey: ["bootstrap"], queryFn: () => api<BootstrapData>("bootstrap"), enabled: open })
  const proxies = useQuery({ queryKey: ["account-editor-proxies", admin], queryFn: () => api<{nodes: Entity[]}>(withScope("proxies", admin)), enabled: open })

  useEffect(() => {
    if (!open) return
    setValues({ ...accountDefaults, owner: Number(row?.owner ?? 0) })
    if (!row?.id) return
    setLoading(true)
    api<Entity>(withScope(`accounts/${row.id}`, admin))
      .then((r) => {
        if (Array.isArray(r.data)) throw new Error("账号详情格式异常，请刷新后重试")
        const d = r.data
        const methods = Array.isArray(d.recovery_methods) ? d.recovery_methods.map(String) : []
        let observed: Record<string, unknown> = {}
        try {
          observed = typeof d.profile_observed === "string"
            ? JSON.parse(d.profile_observed || "{}") as Record<string, unknown>
            : (d.profile_observed as Record<string, unknown> | undefined) ?? {}
        } catch { observed = {} }
        setValues({
          ...accountDefaults,
          ...d,
          automation_schedule: {
            ...structuredClone(defaultCycles),
            ...((d.schedule as { features?: Partial<Cycles> } | undefined)?.features ?? {}),
          },
          shared_cycle: (d.schedule as { shared?: SharedCycle } | undefined)?.shared ?? structuredClone(defaultSharedCycle),
          password: String(d.password ?? ""),
          mailbox_password: String(d.mailbox_password ?? ""),
          dob: String(d.dob ?? "2000-01-01"),
          question1: String(d.question1 ?? ""),
          answer1: String(d.answer1 ?? ""),
          question2: String(d.question2 ?? ""),
          answer2: String(d.answer2 ?? ""),
          question3: String(d.question3 ?? ""),
          answer3: String(d.answer3 ?? ""),
          owner: Number(d.owner ?? 0),
          proxy_id: Number(d.proxy_id ?? 0),
          check_interval: Number(d.check_interval ?? 30),
          min_manual_unlock: Number(d.min_manual_unlock ?? 0),
          enable_check_password_correct: Boolean(d.enable_check_password_correct),
          mailbox_port: Number(d.mailbox_port ?? 993),
          enable: Boolean(d.enable),
          recovery_methods: methods,
          // 后端强制 TLS/IMAPS：只要启用了邮件恢复方式即强制开启 TLS，
          // 同时兼容历史上 mailbox_tls=0 的旧数据（保存后自动纠正回 1）。
          mailbox_tls:
            methods.some((m) => EMAIL_RECOVERY_METHODS.includes(m)) || Boolean(d.mailbox_tls),
          enable_auto_restore_profile: Boolean(d.enable_auto_restore_profile),
          enable_delete_devices: Boolean(
            ((d.schedule as { features?: Record<string, { enabled?: boolean }> } | undefined)?.features?.device_cleanup?.enabled)
              ?? d.enable_delete_devices,
          ),
          profile_family_name: String(d.profile_family_name ?? ""),
          profile_middle_name: String(d.profile_middle_name ?? ""),
          profile_given_name: String(d.profile_given_name ?? ""),
          profile_country: String(d.profile_country ?? ""),
          profile_language: String(d.profile_language ?? ""),
          profile_observed: observed,
        })
      })
      .catch((e: Error) => {
        toast.error(e instanceof Error ? e.message : "账号详情加载失败")
        setOpen(false)
      })
      .finally(() => setLoading(false))
  // 只依赖稳定的账号 ID；列表查询刷新时 row 对象会重新创建，
  // 但不应导致已打开的编辑框再次清空并重新加载详情。
  }, [open, row?.id, admin])

  const mutation = useMutation({
    mutationFn: () => post(`accounts${row?.id ? `/${row.id}` : ""}`, {
      ...values,
      automation_schedule: { features: values.automation_schedule, shared: values.shared_cycle },
      ...(admin ? { scope: "admin" } : {}),
    }),
    onSuccess: (r) => {
      toast.success(r.message)
      setOpen(false)
      onSaved()
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const mailboxTest = useMutation({
    mutationFn: () => post("mailbox/test", {
      account_id: Number(row?.id ?? 0),
      recovery_email: values.recovery_email,
      mailbox_host: values.mailbox_host,
      mailbox_port: values.mailbox_port,
      mailbox_username: values.mailbox_username,
      mailbox_password: values.mailbox_password,
      mailbox_tls: values.mailbox_tls,
      ...(admin ? { scope: "admin" } : {}),
    }),
    onSuccess: (r) => toast.success(r.message),
    onError: (e: Error) => toast.error(e.message),
  })

  const toggleMethod = (method: string, checked: boolean) =>
    setValues((v) => {
      const next = checked
        ? [...new Set([...v.recovery_methods, method])]
        : v.recovery_methods.filter((x) => x !== method)
      const emailRecoveryOn = next.some((m) => EMAIL_RECOVERY_METHODS.includes(m))
      // 勾选/保留邮件恢复方式时强制 TLS，避免被后端“必须使用 TLS/IMAPS”拒绝。
      return { ...v, recovery_methods: next, mailbox_tls: v.mailbox_tls || emailRecoveryOn }
    })

  const emailRecoveryEnabled = values.recovery_methods.some((m) => EMAIL_RECOVERY_METHODS.includes(m))

  return (
    <>
      <Button size="sm" variant={row ? "ghost" : "default"} onClick={() => setOpen(true)}>
        {row ? <Pencil /> : <Plus />}
        {row ? "编辑" : "添加账号"}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{row ? "编辑 Apple 账号" : "添加 Apple 账号"}</DialogTitle>
            <DialogDescription>已完整载入该账号资料，包括账号密码、生日、密保问题答案和救援邮箱凭据；修改后请确认再保存。</DialogDescription>
          </DialogHeader>
          {loading ? (
            <p className="py-8 text-center text-muted-foreground">正在载入账号资料…</p>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              <Field
                label="Apple 账号"
                name={`asset-apple-account-${row?.id ?? "new"}`}
                autoComplete="one-time-code"
                ignorePasswordManager
                value={values.username}
                onChange={(username) => setValues((v) => ({ ...v, username }))}
              />
              <Field
                label="账号密码"
                name={`asset-new-password-${row?.id ?? "new"}`}
                autoComplete="new-password"
                ignorePasswordManager
                placeholder={row ? "已载入原密码" : ""}
                type="text"
                value={values.password}
                onChange={(password) => setValues((v) => ({ ...v, password }))}
              />
              <Field label="出生日期" type="date" value={values.dob} onChange={(dob) => setValues((v) => ({ ...v, dob }))} />
              <Field label="内部备注" value={values.remark} onChange={(remark) => setValues((v) => ({ ...v, remark }))} />
              <Field
                label="前端备注"
                value={values.frontend_remark}
                onChange={(frontend_remark) => setValues((v) => ({ ...v, frontend_remark }))}
              />
              <div className="space-y-2"><Label>当前用户</Label><Input value={String(boot.data?.data.user?.username ?? "加载中…")} readOnly /></div>
              <Field
                label="旧调度兼容间隔（分钟）"
                type="number"
                value={String(values.check_interval)}
                onChange={(check_interval) => setValues((v) => ({ ...v, check_interval: Number(check_interval) }))}
              />
              <div className="space-y-2"><Label>代理节点</Label><select className="h-8 w-full rounded-lg border bg-background px-2.5 text-sm" value={values.proxy_id} onChange={e=>setValues(v=>({...v,proxy_id:Number(e.target.value)}))}><option value={0}>自动分配 / 不指定</option>{(proxies.data?.data.nodes??[]).map(node=><option key={String(node.id)} value={Number(node.id)}>{String(node.name??`节点 ${node.id}`)} · {String(node.protocol??"").toUpperCase()}</option>)}</select></div>
              <Field label="最低手动解锁间隔（分钟）" type="number" value={String(values.min_manual_unlock)} onChange={(min_manual_unlock) => setValues((v) => ({ ...v, min_manual_unlock: Number(min_manual_unlock) }))} />
              <div className="space-y-2 sm:col-span-2"><Label>密保问题与答案</Label><div className="grid gap-3 rounded-lg border p-3 sm:grid-cols-2">
                <Field label="密保问题 1" value={values.question1} onChange={(question1) => setValues((v) => ({ ...v, question1 }))} />
                <Field label="密保答案 1" value={values.answer1} onChange={(answer1) => setValues((v) => ({ ...v, answer1 }))} />
                <Field label="密保问题 2" value={values.question2} onChange={(question2) => setValues((v) => ({ ...v, question2 }))} />
                <Field label="密保答案 2" value={values.answer2} onChange={(answer2) => setValues((v) => ({ ...v, answer2 }))} />
                <Field label="密保问题 3" value={values.question3} onChange={(question3) => setValues((v) => ({ ...v, question3 }))} />
                <Field label="密保答案 3" value={values.answer3} onChange={(answer3) => setValues((v) => ({ ...v, answer3 }))} />
              </div></div>
              <div className="space-y-2 sm:col-span-2">
                <Label>允许使用的恢复方式</Label>
                <div className="grid gap-2 rounded-lg border p-3 sm:grid-cols-3">
                  {[
                    ["email_password", "邮件修改密码"],
                    ["email_security", "邮件解除双重认证"],
                    ["account_login", "登录账号处理"],
                  ].map(([key, label]) => (
                    <label key={key} className="flex items-center gap-2 text-sm">
                      <Checkbox checked={values.recovery_methods.includes(key)} onCheckedChange={(checked) => toggleMethod(key, Boolean(checked))} />
                      {label}
                    </label>
                  ))}
                </div>
              </div>
              <Field
                label="恢复邮箱"
                name={`asset-recovery-email-${row?.id ?? "new"}`}
                autoComplete="off"
                ignorePasswordManager
                value={values.recovery_email}
                onChange={(recovery_email) => setValues((v) => ({ ...v, recovery_email }))}
              />
              <Field
                label="IMAP 主机"
                name={`asset-imap-host-${row?.id ?? "new"}`}
                autoComplete="off"
                value={values.mailbox_host}
                onChange={(mailbox_host) => setValues((v) => ({ ...v, mailbox_host }))}
              />
              <Field
                label="邮箱用户名"
                name={`asset-mailbox-user-${row?.id ?? "new"}`}
                autoComplete="off"
                ignorePasswordManager
                value={values.mailbox_username}
                onChange={(mailbox_username) => setValues((v) => ({ ...v, mailbox_username }))}
              />
              <Field
                label="邮箱密码"
                name={`asset-mailbox-password-${row?.id ?? "new"}`}
                autoComplete="new-password"
                ignorePasswordManager
                type="text"
                value={values.mailbox_password}
                onChange={(mailbox_password) => setValues((v) => ({ ...v, mailbox_password }))}
              />
              <Field
                label="IMAP 端口"
                type="number"
                value={String(values.mailbox_port)}
                onChange={(mailbox_port) => setValues((v) => ({ ...v, mailbox_port: Number(mailbox_port) }))}
              />
              <div className="space-y-2 sm:col-span-2">
                <Toggle
                  label="使用 TLS/IMAPS 加密连接"
                  hint={emailRecoveryEnabled ? "已启用邮件恢复，出于安全系统强制使用 TLS/IMAPS，无法关闭" : undefined}
                  value={values.mailbox_tls}
                  disabled={emailRecoveryEnabled}
                  onChange={(mailbox_tls) => setValues((v) => ({ ...v, mailbox_tls }))}
                />
              </div>
              <div className="flex items-center justify-between gap-3 rounded-lg border border-dashed p-3 sm:col-span-2">
                <p className="text-xs text-muted-foreground">使用当前输入内容测试 IMAPS 登录与收件箱只读访问，不会保存配置或读取邮件。</p>
                <Button type="button" size="sm" variant="outline" disabled={mailboxTest.isPending || !values.recovery_email || !values.mailbox_host || !values.mailbox_username || (!values.mailbox_password && !row?.id) || !values.mailbox_tls} onClick={() => mailboxTest.mutate()}>
                  {mailboxTest.isPending ? "测试中…" : "测试连通性"}
                </Button>
              </div>
              <div className="space-y-3 rounded-lg border p-4 sm:col-span-2">
                <div>
                  <Label>个人资料保护基线</Label>
                  <p className="mt-1 text-xs text-muted-foreground">这里保存的内容就是自动恢复目标；检测到 Apple 资料变化后会直接恢复，出生日期使用上方填写的值。</p>
                </div>
                <div className="grid gap-3 sm:grid-cols-3">
                  <Field label="姓氏" value={values.profile_family_name} onChange={(profile_family_name) => setValues(v => ({ ...v, profile_family_name }))} />
                  <Field label="中间名（可选）" value={values.profile_middle_name} onChange={(profile_middle_name) => setValues(v => ({ ...v, profile_middle_name }))} />
                  <Field label="名字" value={values.profile_given_name} onChange={(profile_given_name) => setValues(v => ({ ...v, profile_given_name }))} />
                  <Field label="国家/地区（与 Apple 显示一致）" value={values.profile_country} onChange={(profile_country) => setValues(v => ({ ...v, profile_country }))} />
                  <Field label="语言（完整显示名称）" value={values.profile_language} onChange={(profile_language) => setValues(v => ({ ...v, profile_language }))} />
                </div>
                <div className="rounded-md bg-muted/50 p-3 text-xs text-muted-foreground">
                  <div className="flex flex-wrap items-center justify-between gap-2 font-medium text-foreground">
                    <span>最近观测值</span>
                    <span className="font-normal text-muted-foreground">{String(values.profile_observed.observed_at ?? "尚未检测")}</span>
                  </div>
                  <div className="mt-1 grid gap-1 sm:grid-cols-2">
                    <span>姓名：{String(values.profile_observed.name ?? "尚未读取")}</span>
                    <span>出生日期：{String(values.profile_observed.dob ?? "尚未读取")}</span>
                    <span>国家/地区：{String(values.profile_observed.country ?? "尚未读取")}</span>
                    <span>语言：{String(values.profile_observed.language ?? "尚未读取")}</span>
                  </div>
                  {values.profile_observed.results && typeof values.profile_observed.results === "object" ? (
                    <div className="mt-2 space-y-1 border-t pt-2">
                      {Object.entries(values.profile_observed.results as Record<string, unknown>).map(([key, raw]) => {
                        const item = raw && typeof raw === "object" ? raw as Record<string, unknown> : {}
                        return <div key={key}>{String(item.message ?? "")}</div>
                      })}
                    </div>
                  ) : null}
                  {values.profile_observed.summary && !values.profile_observed.results ? <p className="mt-2 text-foreground">{String(values.profile_observed.summary)}</p> : null}
                </div>
                <p className="text-xs text-amber-600">启用自动恢复资料后，检测到差异会直接修改 Apple 资料；请确保目标资料填写正确。</p>
              </div>
              <AccountCycles cycles={values.automation_schedule} onChange={automation_schedule => setValues(v => ({ ...v, automation_schedule }))}
                shared={values.shared_cycle} onSharedChange={shared_cycle => setValues(v => ({ ...v, shared_cycle }))}
                flags={values} setFlag={(flag, value) => setValues(v => ({ ...v, [flag]: value }))} accountId={row?.id} admin={admin} open={open} />
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>取消</Button>
            <Button
              disabled={loading || mutation.isPending || !validCycles(values.automation_schedule) || !validSharedCycle(values.shared_cycle) || !values.username || (!row && !values.password)}
              onClick={() => mutation.mutate()}
            >
              保存账号
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

type CredentialValues = {
  password: string
  question1: string
  answer1: string
  question2: string
  answer2: string
  question3: string
  answer3: string
  mailbox_password: string
}

/**
 * 「查看密码」：登录会话与账号归属校验通过后直接展示凭据。
 * 不再要求二次输入面板密码（移动端 prompt 会被键盘自动大写/补空格而误判为密码错误），
 * 每次打开都会写入 credential_audit 审计日志。
 */
export function AccountCredentials({ row, admin }: { row: Entity; admin: boolean }) {
  const [open, setOpen] = useState(false)
  const query = useQuery({
    queryKey: ["account-credentials", row.id, admin],
    queryFn: () => post<CredentialValues>(withScope(`accounts/${row.id}/reveal-password`, admin), {}),
    enabled: open,
    staleTime: 0,
    gcTime: 0,
  })
  const copy = async (label: string, value: string) => {
    if (!value) return
    try { await navigator.clipboard.writeText(value); toast.success(`${label}已复制`) }
    catch { toast.error("复制失败，请长按选中后手动复制") }
  }
  const data = query.data?.data
  const stored = (value: unknown) => String(value ?? "")
  const pairs: Array<{ label: string; value: string }> = [
    { label: "Apple 密码", value: stored(data?.password) },
    { label: "救援邮箱密码", value: stored(data?.mailbox_password) },
  ]
  const questions: Array<{ question: unknown; answer: unknown }> = [
    { question: data?.question1, answer: data?.answer1 },
    { question: data?.question2, answer: data?.answer2 },
    { question: data?.question3, answer: data?.answer3 },
  ]
  const credentialRow = (label: string, value: string) => (
    <div className="space-y-1" key={label}>
      <div className="flex items-center justify-between gap-2">
        <Label>{label}</Label>
        <Button size="sm" variant="ghost" disabled={!value} onClick={() => copy(label, value)}><Copy className="size-4" />复制</Button>
      </div>
      <Input readOnly value={value || "未记录"} className={value ? "font-mono" : "text-muted-foreground"} onFocus={event => event.currentTarget.select()} />
    </div>
  )
  return (
    <>
      <Button size="sm" variant="ghost" onClick={() => setOpen(true)}><Eye />查看密码</Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>账号凭据</DialogTitle>
            <DialogDescription>{String(row.username ?? "")} 的 Apple 密码、密保答案与救援邮箱密码；本次查看会记录审计日志。</DialogDescription>
          </DialogHeader>
          {query.isLoading ? <p className="py-6 text-center text-sm text-muted-foreground">正在读取凭据…</p>
            : query.error ? (
              <div className="space-y-3 rounded-lg border border-destructive/40 p-3 text-sm">
                <p className="text-destructive">{query.error instanceof Error ? query.error.message : "凭据读取失败，请稍后重试"}</p>
                <Button size="sm" variant="outline" onClick={() => query.refetch()}>重试</Button>
              </div>
            )
              : (
                <div className="space-y-4">
                  {pairs.map(pair => credentialRow(pair.label, pair.value))}
                  <div className="space-y-3 rounded-lg border p-3">
                    <p className="text-sm font-medium">密保问题与答案</p>
                    {questions.map((item, index) => (
                      <div className="space-y-1" key={index}>
                        <p className="text-xs text-muted-foreground">{stored(item.question) || `密保问题 ${index + 1}`}</p>
                        <div className="flex items-center gap-2">
                          <Input readOnly value={stored(item.answer) || "未记录"} className={stored(item.answer) ? "font-mono" : "text-muted-foreground"} onFocus={event => event.currentTarget.select()} />
                          <Button size="sm" variant="ghost" disabled={!stored(item.answer)} onClick={() => copy(`密保答案 ${index + 1}`, stored(item.answer))}><Copy className="size-4" />复制</Button>
                        </div>
                      </div>
                    ))}
                  </div>
                  <p className="text-xs text-muted-foreground">救援邮箱：{String(row.recovery_email ?? "") || "未配置"}；启用邮件恢复后，邮箱密码用于自动化改密与安全回退。</p>
                </div>
              )}
          <DialogFooter><Button variant="outline" onClick={() => setOpen(false)}>关闭</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

type ShareValues = {
  share_link: string
  password: string
  remark: string
  duration_minutes: number
  owner: number
  account_list: number[]
  access_tracking: boolean
  group_id: number
  embed_enabled: boolean
  embed_origins: string
  template_id: number
}

const shareDefaults: ShareValues = {
  share_link: "",
  password: "",
  remark: "",
  duration_minutes: 0,
  owner: 0,
  account_list: [],
  access_tracking: true,
  group_id: 0,
  embed_enabled: false,
  embed_origins: "",
  template_id: 0,
}

function shareUrl(link: string) {
  return `${window.location.origin}/share/${link}`
}

function copyText(text: string) {
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text).then(() => toast.success("已复制"), () => toast.error("复制失败，请手动复制"))
  } else {
    toast.error("当前浏览器不支持自动复制，请手动复制")
  }
}

/**
 * 「页面模板」下拉选项。只列出真实模板（与「分享页模板」页数量一致），
 * 默认模板追加“（默认）”标记；fallback 用于把未指定模板（0）的旧分享显示为默认模板。
 */
export function pageTemplateChoices(rows: Entity[]) {
  const options = rows.map(row => ({ value: Number(row.id), label: `${String(row.name ?? "未命名")}${row.effective_default ? "（默认）" : ""}` }))
  const fallback = rows.find(row => Boolean(row.effective_default))?.id
  return { options, fallback: fallback === undefined ? 0 : Number(fallback) }
}

export function ShareEditor({ row, admin, onSaved }: { row?: Entity; admin: boolean; onSaved: () => void }) {
  const [open, setOpen] = useState(false)
  const [values, setValues] = useState(shareDefaults)
  const [created, setCreated] = useState<{ share_link: string; url: string } | null>(null)
  const boot = useQuery({ queryKey: ["bootstrap"], queryFn: () => api<BootstrapData>("bootstrap"), enabled: open })
  const groups = useQuery({ queryKey: ["share-groups-current"], queryFn: () => api<Entity[]>("share-groups"), enabled: open })
  const templates = useQuery({ queryKey: ["templates"], queryFn: () => api<Entity[]>("templates"), enabled: open })
  const pageTemplates = pageTemplateChoices(templates.data?.data ?? [])
  const hasOpaqueTemplate = values.template_id > 0 && !pageTemplates.options.some(option => option.value === values.template_id)
  const accounts = useQuery({
    queryKey: ["share-editor-accounts"],
    queryFn: () => api<Entity[]>("accounts"),
    enabled: open,
  })
  useEffect(() => {
    if (!open) return
    setValues({ ...shareDefaults, owner: Number(row?.owner ?? 0) })
    setCreated(row?.share_link ? { share_link: String(row.share_link), url: shareUrl(String(row.share_link)) } : null)
    if (row?.id)
      api<Entity>(withScope(`shares/${row.id}`, admin)).then((r) => {
        const d = r.data
        setValues({
          ...shareDefaults,
          ...d,
          password: "",
          owner: Number(d.owner ?? 0),
          duration_minutes: Number(d.duration_minutes ?? 0),
          account_list: Array.isArray(d.account_list)
            ? d.account_list.map(Number)
            : String(d.account_list ?? "").split(",").filter(Boolean).map(Number),
          access_tracking: Boolean(d.access_tracking),
          embed_enabled: Boolean(d.embed_enabled), embed_origins: String(d.embed_origins ?? ""),
          group_id: Number(d.group_id ?? 0), template_id: Number(d.template_id ?? 0),
        })
        if (d.share_link) setCreated({ share_link: String(d.share_link), url: shareUrl(String(d.share_link)) })
      }).catch((e: Error) => toast.error(e.message))
  }, [open, row, admin])
  const mutation = useMutation({
    mutationFn: () => post<{ share_link?: string; url?: string }>(`shares${row?.id ? `/${row.id}` : ""}`, { ...values, ...(admin ? { scope: "admin" } : {}) }),
    onSuccess: (r) => {
      toast.success(r.message)
      if (r.data?.share_link) setCreated({ share_link: String(r.data.share_link), url: String(r.data.url ?? shareUrl(String(r.data.share_link))) })
      onSaved()
    },
    onError: (e: Error) => toast.error(e.message),
  })
  const toggleAccount = (id: number, checked: boolean) =>
    setValues((v) => ({
      ...v,
      account_list: checked ? [...new Set([...v.account_list, id])] : v.account_list.filter((x) => x !== id),
    }))
  const copyCreated = () => { if (created) copyText(created.url) }
  return (
    <>
      <Button size="sm" variant={row ? "ghost" : "default"} onClick={() => setOpen(true)}>
        {row ? <Pencil /> : <Plus />}
        {row ? "编辑" : "新建分享"}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>{row ? "编辑分享页" : "新建分享页"}</DialogTitle>
            <DialogDescription>公开地址为 /share/分享代码；分享代码留空将自动生成，访问密码留空表示不设置。</DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field
              label="分享代码"
              value={values.share_link}
              onChange={(share_link) => setValues((v) => ({ ...v, share_link }))}
              placeholder="留空自动生成"
            />
            <Field
              label="访问密码"
              type="password"
              value={values.password}
              onChange={(password) => setValues((v) => ({ ...v, password }))}
            />
            <Field label="备注" value={values.remark} onChange={(remark) => setValues((v) => ({ ...v, remark }))} />
            <Field
              label="首次访问后有效分钟数"
              type="number"
              value={String(values.duration_minutes)}
              onChange={(duration_minutes) => setValues((v) => ({ ...v, duration_minutes: Number(duration_minutes) }))}
            />
            <div className="space-y-2"><Label>当前用户</Label><Input value={String(boot.data?.data.user?.username ?? "加载中…")} readOnly /></div>
            <div className="space-y-2"><Label>所属分享组</Label><select className="h-8 w-full rounded-lg border bg-background px-2.5 text-sm" value={values.group_id} onChange={e=>setValues(v=>({...v,group_id:Number(e.target.value)}))}><option value={0}>不加入组</option>{(groups.data?.data??[]).map(group=><option key={String(group.id)} value={Number(group.id)}>{String(group.name)}</option>)}</select></div>
            <div className="space-y-2"><Label>页面模板</Label><select className="h-8 w-full rounded-lg border bg-background px-2.5 text-sm" value={values.template_id} disabled={values.group_id>0} onChange={e=>setValues(v=>({...v,template_id:Number(e.target.value)}))}><option value={0}>使用默认模板</option>{hasOpaqueTemplate&&<option value={values.template_id}>保持当前私有模板</option>}{pageTemplates.options.map(option=><option key={String(option.value)} value={option.value}>{option.label}</option>)}</select></div>
            {values.group_id>0&&<p className="text-xs text-muted-foreground sm:col-span-2">账号、密码、有效期和模板由所属分享组统一控制；编辑组后，所有所属链接会同步更新。</p>}
            <Toggle
              label="记录首次访问"
              value={values.access_tracking}
              onChange={(access_tracking) => setValues((v) => ({ ...v, access_tracking }))}
            />
            <Toggle label="允许 iframe 嵌入" value={values.embed_enabled} onChange={embed_enabled => setValues(v => ({ ...v, embed_enabled }))} />
            {values.embed_enabled && <div className="space-y-2 sm:col-span-2">
              <Label htmlFor="embed-origins">允许嵌入的网站</Label>
              <textarea id="embed-origins" className="min-h-24 w-full rounded-lg border bg-background p-2 text-sm" value={values.embed_origins} onChange={e => setValues(v => ({ ...v, embed_origins: e.target.value }))} placeholder="https://shop.example.com" />
              <p className="text-xs text-muted-foreground">每行一个完整 http(s) 网站来源，包含非默认端口；不支持路径和通配符。留空允许任意网站嵌入。嵌入页仅显示账号密码，仍需验证分享访问密码。</p>
            </div>}
            <div className="space-y-2 sm:col-span-2">
              <Label>关联账号</Label>
              <div className="max-h-40 space-y-2 overflow-y-auto rounded-lg border p-3">
                {(accounts.data?.data ?? []).map((account) => (
                  <label className="flex items-center gap-2 text-sm" key={String(account.id)}>
                    <Checkbox
                      checked={values.account_list.includes(Number(account.id))}
                      onCheckedChange={(checked) => toggleAccount(Number(account.id), Boolean(checked))}
                    />
                    <span>{String(account.username ?? account.id)}</span>
                  </label>
                ))}
              </div>
            </div>
            {created && (
              <div className="space-y-2 rounded-lg border p-3 sm:col-span-2">
                <Label>分享地址</Label>
                {values.embed_enabled && <div className="space-y-2">
                  <Label htmlFor="embed-code">iframe 嵌入代码（保存设置后生效）</Label>
                  <textarea id="embed-code" className="min-h-20 w-full rounded-lg border bg-background p-2 text-xs" readOnly value={`<iframe src="${shareUrl(created.share_link)}/embed" title="共享账号" width="100%" height="240" style="border:0" allow="clipboard-write"></iframe>`} />
                  <Button type="button" size="sm" variant="outline" onClick={() => copyText(`<iframe src="${shareUrl(created.share_link)}/embed" title="共享账号" width="100%" height="240" style="border:0" allow="clipboard-write"></iframe>`)}>复制嵌入代码</Button>
                </div>}

                <div className="flex items-center gap-2">
                  <Input value={created.url} readOnly className="flex-1" />
                  <Button type="button" size="sm" variant="outline" onClick={copyCreated}><Copy className="size-4" />复制</Button>
                </div>
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>关闭</Button>
            <Button
              disabled={mutation.isPending || !values.account_list.length}
              onClick={() => mutation.mutate()}
            >
              保存分享
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

export function BatchShareEditor({ admin, onSaved }: { admin: boolean; onSaved: () => void }) {
  const [open, setOpen] = useState(false)
  const boot = useQuery({ queryKey: ["bootstrap"], queryFn: () => api<BootstrapData>("bootstrap"), enabled: open })
  const [groupId, setGroupId] = useState(0)
  const [accountIds, setAccountIds] = useState<number[]>([])
  const [quantity, setQuantity] = useState(1)
  const [expiryMode, setExpiryMode] = useState<"never" | "first_access" | "fixed">("never")
  const [durationMinutes, setDurationMinutes] = useState(30)
  const [expire, setExpire] = useState("")
  const [remark, setRemark] = useState("")
  const [baseUrl, setBaseUrl] = useState("")
  const [result, setResult] = useState<string[] | null>(null)
  const accounts = useQuery({
    queryKey: ["batch-share-accounts"],
    queryFn: () => api<Entity[]>("accounts"),
    enabled: open,
  })
  const groups = useQuery({ queryKey: ["batch-share-groups"], queryFn: () => api<Entity[]>("share-groups"), enabled: open })
  useEffect(() => { if (open) { setResult(null); setGroupId(0); setAccountIds([]); setBaseUrl("") } }, [open])
  const toggleAccount = (id: number, checked: boolean) =>
    setAccountIds((prev) => (checked ? [...new Set([...prev, id])] : prev.filter((x) => x !== id)))
  const mutation = useMutation({
    mutationFn: () => post(withScope("shares/batch-create", admin), {
      group_id: groupId || undefined,
      account_ids: accountIds,
      quantity,
      expiry_mode: expiryMode,
      duration_minutes: expiryMode === "first_access" ? durationMinutes : 0,
      expire: expiryMode === "fixed" ? expire.replace("T", " ") : undefined,
      remark,
      base_url: baseUrl.trim() || undefined,
    }),
    onSuccess: (r) => { setResult((r.data as string[]) ?? []); toast.success(r.message); onSaved() },
    onError: (e: Error) => toast.error(e.message),
  })
  const copyAll = () => { if (result) copyText(result.join("\n")) }
  const visibleAccounts = accounts.data?.data ?? []
  const selectedGroup = (groups.data?.data ?? []).find(group => Number(group.id) === groupId)
  const selectGroup = (id: number) => {
    setGroupId(id)
    if (!id) { setAccountIds([]); return }
    const group = (groups.data?.data ?? []).find(item => Number(item.id) === id)
    if (group && Array.isArray(group.account_list)) setAccountIds(group.account_list.map(Number))
  }
  return (
    <>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}><Plus />批量生成</Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>批量生成分享链接</DialogTitle>
            <DialogDescription>为选中的账号一次性生成多个独立分享链接，生成后请及时复制保存。</DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2"><Label>当前用户</Label><Input value={String(boot.data?.data.user?.username ?? "加载中…")} readOnly /></div>
            <Field label="生成数量" type="number" value={String(quantity)} onChange={(v) => setQuantity(Math.max(1, Math.min(100, Number(v))))} />
            <div className="space-y-2 sm:col-span-2"><Label>直接加入分享组</Label><select className="h-8 w-full rounded-lg border bg-background px-2.5 text-sm" value={groupId} onChange={e=>selectGroup(Number(e.target.value))}><option value={0}>不加入组，使用本次设置</option>{(groups.data?.data??[]).map(group=><option key={String(group.id)} value={Number(group.id)}>{String(group.name)} · {Number(group.link_count??0)} 条链接</option>)}</select>{selectedGroup&&<p className="text-xs text-muted-foreground">将使用“{String(selectedGroup.name)}”的账号、密码、有效期、分享网址和页面模板。</p>}</div>
            {!groupId && <>
            <div className="space-y-2 sm:col-span-2">
              <Label>过期方式</Label>
              <select className="h-8 w-full rounded-lg border bg-background px-2.5 text-sm" value={expiryMode} onChange={(e) => setExpiryMode(e.target.value as typeof expiryMode)}>
                <option value="never">永不过期</option>
                <option value="first_access">首次访问后失效</option>
                <option value="fixed">固定时间过期</option>
              </select>
            </div>
            {expiryMode === "first_access" && <Field label="首次访问后有效分钟数" type="number" value={String(durationMinutes)} onChange={(v) => setDurationMinutes(Number(v))} />}
            {expiryMode === "fixed" && <Field label="过期时间" type="datetime-local" value={expire} onChange={setExpire} />}
            <Field label="分享网址（可选）" value={baseUrl} onChange={setBaseUrl} placeholder="https://share.example.com，留空用默认站点" />
            </>}
            <Field label="备注" value={remark} onChange={setRemark} />
            <div className="space-y-2 sm:col-span-2">
              <Label>{groupId ? "组内账号" : "选择账号"}（{accountIds.length} 个）</Label>
              <div className="max-h-40 space-y-2 overflow-y-auto rounded-lg border p-3">
                {visibleAccounts.length === 0 && <p className="text-sm text-muted-foreground">当前用户暂无账号可选</p>}
                {visibleAccounts.map((account) => (
                  <label className="flex items-center gap-2 text-sm" key={String(account.id)}>
                    <Checkbox disabled={groupId>0} checked={accountIds.includes(Number(account.id))} onCheckedChange={(checked) => toggleAccount(Number(account.id), Boolean(checked))} />
                    <span>{String(account.username ?? account.id)}</span>
                  </label>
                ))}
              </div>
            </div>
            {result && result.length > 0 && (
              <div className="space-y-2 rounded-lg border p-3 sm:col-span-2">
                <div className="flex items-center justify-between"><Label>生成的分享地址（共 {result.length} 条，一行一条）</Label><Button type="button" size="sm" variant="outline" onClick={copyAll}><Copy className="size-4" />一键复制全部</Button></div>
                <Textarea readOnly value={result.join("\n")} rows={Math.min(result.length, 8)} className="min-h-[120px] font-mono text-xs leading-relaxed" />
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>关闭</Button>
            <Button disabled={mutation.isPending || (!groupId&&!accountIds.length) || quantity < 1} onClick={() => mutation.mutate()}>生成并{groupId ? "加入组" : "保存"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

type GroupValues={name:string;template_id:number;account_list:number[];password:string;duration_minutes:number;expire:string;base_url:string;access_tracking:boolean}
const groupDefaults:GroupValues={name:"",template_id:0,account_list:[],password:"",duration_minutes:0,expire:"",base_url:"",access_tracking:true}

export function ShareGroupManager({admin,onSaved}:{admin:boolean;onSaved:()=>void}) {
  const [open,setOpen]=useState(false)
  const [editing,setEditing]=useState<Entity|undefined>()
  const [values,setValues]=useState<GroupValues>(groupDefaults)
  const client=useQueryClient()
  const groups=useQuery({queryKey:["share-groups",admin],queryFn:()=>api<Entity[]>(withScope("share-groups",admin)),enabled:open})
  const accounts=useQuery({queryKey:["share-group-accounts"],queryFn:()=>api<Entity[]>("accounts"),enabled:open})
  const templates=useQuery({queryKey:["templates"],queryFn:()=>api<Entity[]>("templates"),enabled:open})
  const pageTemplates=pageTemplateChoices(templates.data?.data??[])
  const hasOpaqueTemplate=values.template_id>0&&!pageTemplates.options.some(option=>option.value===values.template_id)
  const edit=(row?:Entity)=>{setEditing(row);setValues(row?{...groupDefaults,name:String(row.name??""),template_id:Number(row.template_id??0),account_list:Array.isArray(row.account_list)?row.account_list.map(Number):[],duration_minutes:Number(row.duration_minutes??0),expire:String(row.expire??"").replace(" ","T"),base_url:String(row.base_url??""),access_tracking:Boolean(row.access_tracking)}:groupDefaults)}
  const refreshGroups=()=>{client.invalidateQueries({queryKey:["share-groups"]});client.invalidateQueries({queryKey:["share-groups-current"]})}
  const save=useMutation({mutationFn:()=>post(withScope(`share-groups${editing?.id?`/${editing.id}`:""}`,admin),{...values,expire:values.expire.replace("T"," ")||undefined}),onSuccess:r=>{toast.success(r.message);edit();refreshGroups();onSaved()},onError:(e:Error)=>toast.error(e.message)})
  const remove=useMutation({mutationFn:(id:string|number)=>api(withScope(`share-groups/${id}`,admin),{method:"DELETE"}),onSuccess:r=>{toast.success(r.message);refreshGroups();onSaved()},onError:(e:Error)=>toast.error(e.message)})
  const toggle=(id:number,checked:boolean)=>setValues(v=>({...v,account_list:checked?[...new Set([...v.account_list,id])]:v.account_list.filter(x=>x!==id)}))
  return <><Button size="sm" variant="outline" onClick={()=>{setOpen(true);edit()}}><Plus/>分享组</Button><Dialog open={open} onOpenChange={setOpen}><DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-3xl"><DialogHeader><DialogTitle>分享组管理</DialogTitle><DialogDescription>链接加入组后，编辑组的账号、密码、有效期或模板会同步到组内全部链接。</DialogDescription></DialogHeader><div className="grid gap-5 md:grid-cols-[.8fr_1.2fr]"><div className="space-y-2"><Button className="w-full" variant={!editing?"secondary":"outline"} onClick={()=>edit()}>新建分享组</Button>{(groups.data?.data??[]).map(group=><div key={String(group.id)} className="flex items-center justify-between rounded-lg border p-3"><button className="min-w-0 text-left" onClick={()=>edit(group)}><p className="truncate text-sm font-medium">{String(group.name)}</p><p className="text-xs text-muted-foreground">{String(group.template_name??"默认模板")} · {Number(group.link_count??0)} 条链接</p></button><DeleteAction title="删除分享组？" description="组内分享链接会保留并解除分组。" onConfirm={()=>remove.mutate(group.id!)}/></div>)}</div><div className="grid gap-3 sm:grid-cols-2"><Field label="组名称" value={values.name} onChange={name=>setValues(v=>({...v,name}))}/><div className="space-y-2"><Label>关联模板</Label><select className="h-8 w-full rounded-lg border bg-background px-2.5 text-sm" value={values.template_id} onChange={e=>setValues(v=>({...v,template_id:Number(e.target.value)}))}><option value={0}>使用默认模板</option>{hasOpaqueTemplate&&<option value={values.template_id}>保持当前私有模板</option>}{pageTemplates.options.map(option=><option key={String(option.value)} value={option.value}>{option.label}</option>)}</select></div><Field label="访问密码（留空保持原值）" type="password" value={values.password} onChange={password=>setValues(v=>({...v,password}))}/><Field label="首次访问后有效分钟数" type="number" value={String(values.duration_minutes)} onChange={duration_minutes=>setValues(v=>({...v,duration_minutes:Number(duration_minutes)}))}/><Field label="固定到期时间（可选）" type="datetime-local" value={values.expire} onChange={expire=>setValues(v=>({...v,expire}))}/><Field label="分享网址（可选）" value={values.base_url} onChange={base_url=>setValues(v=>({...v,base_url}))}/><Toggle label="记录首次访问" value={values.access_tracking} onChange={access_tracking=>setValues(v=>({...v,access_tracking}))}/><div className="space-y-2 sm:col-span-2"><Label>组内账号</Label><div className="max-h-44 space-y-2 overflow-y-auto rounded-lg border p-3">{(accounts.data?.data??[]).map(a=><label key={String(a.id)} className="flex items-center gap-2 text-sm"><Checkbox checked={values.account_list.includes(Number(a.id))} onCheckedChange={checked=>toggle(Number(a.id),Boolean(checked))}/>{String(a.username??a.id)}</label>)}</div></div><Button className="sm:col-span-2" disabled={save.isPending||!values.name||!values.account_list.length} onClick={()=>save.mutate()}>{editing?"保存并同步组内链接":"创建分享组"}</Button></div></div></DialogContent></Dialog></>
}

type UserValues = {
  username: string
  password: string
  tg_bot_token: string
  tg_chat_id: string
  tg_notify_mode: "success" | "failure" | "all"
  is_admin: boolean
  status: boolean
  max_accounts: number
  max_share_pages: number
  min_manual_interval: number
  balance: string
  vip_expire_at: string
}

const userDefaults: UserValues = {
  username: "",
  password: "",
  tg_bot_token: "",
  tg_chat_id: "",
  tg_notify_mode: "all",
  is_admin: false,
  status: true,
  max_accounts: 5,
  max_share_pages: 1,
  min_manual_interval: 0,
  balance: "0.00",
  vip_expire_at: "",
}

export function UserEditor({ row, onSaved }: { row?: Entity; onSaved: () => void }) {
  const [open, setOpen] = useState(false)
  const [values, setValues] = useState(userDefaults)
  useEffect(() => {
    if (open)
      setValues({
        ...userDefaults,
        ...row,
        password: "",
        username: String(row?.username ?? ""),
        tg_bot_token: String(row?.tg_bot_token ?? ""),
        tg_chat_id: String(row?.tg_chat_id ?? ""),
        tg_notify_mode: ["success", "failure", "all"].includes(String(row?.tg_notify_mode)) ? row?.tg_notify_mode as UserValues["tg_notify_mode"] : "all",
        is_admin: Boolean(row?.is_admin),
        status: Boolean(row?.status),
        max_accounts: Number(row?.max_accounts ?? 5),
        max_share_pages: Number(row?.max_share_pages ?? 1),
        min_manual_interval: Number(row?.min_manual_interval ?? 0),
        balance: String(row?.balance ?? "0.00"),
        vip_expire_at: String(row?.vip_expire_at ?? ""),
      })
  }, [open, row])
  const mutation = useMutation({
    mutationFn: () => post(`admin/users${row?.id ? `/${row.id}` : ""}`, values),
    onSuccess: (r) => {
      toast.success(r.message)
      setOpen(false)
      onSaved()
    },
    onError: (e: Error) => toast.error(e.message),
  })
  return (
    <>
      <Button size="sm" variant={row ? "ghost" : "default"} onClick={() => setOpen(true)}>
        {row ? <Pencil /> : <Plus />}
        {row ? "编辑" : "添加用户"}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{row ? "编辑用户与权限" : "添加平台用户"}</DialogTitle>
            <DialogDescription>密码留空时保留当前密码。</DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="用户名" value={values.username} onChange={(username) => setValues((v) => ({ ...v, username }))} />
            <Field
              label={row ? "新密码" : "初始密码"}
              type="password"
              value={values.password}
              onChange={(password) => setValues((v) => ({ ...v, password }))}
            />
            <Field label="Telegram Bot Token" type="password" value={values.tg_bot_token} onChange={(tg_bot_token) => setValues((v) => ({ ...v, tg_bot_token }))} />
            <Field label="Telegram Chat ID" value={values.tg_chat_id} onChange={(tg_chat_id) => setValues((v) => ({ ...v, tg_chat_id }))} />
            <div className="space-y-2 sm:col-span-2"><Label>Telegram 任务结果推送</Label><select className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm" value={values.tg_notify_mode} onChange={(e)=>setValues(v=>({...v,tg_notify_mode:e.target.value as UserValues["tg_notify_mode"]}))}><option value="all">成功和失败都推送</option><option value="success">仅推送成功消息</option><option value="failure">仅推送失败消息</option></select></div>
            <Field
              label="账号配额"
              type="number"
              value={String(values.max_accounts)}
              onChange={(max_accounts) => setValues((v) => ({ ...v, max_accounts: Number(max_accounts) }))}
            />
            <Field
              label="分享配额"
              type="number"
              value={String(values.max_share_pages)}
              onChange={(max_share_pages) => setValues((v) => ({ ...v, max_share_pages: Number(max_share_pages) }))}
            />
            <Field
              label="最低手动间隔"
              type="number"
              value={String(values.min_manual_interval)}
              onChange={(min_manual_interval) => setValues((v) => ({ ...v, min_manual_interval: Number(min_manual_interval) }))}
            />
            <Field label="余额" type="number" value={values.balance} onChange={(balance) => setValues((v) => ({ ...v, balance }))} />
            <Field
              label="有效期"
              type="datetime-local"
              value={values.vip_expire_at?.replace(" ", "T")}
              onChange={(vip_expire_at) => setValues((v) => ({ ...v, vip_expire_at: vip_expire_at.replace("T", " ") }))}
            />
            <Toggle label="账号启用" value={values.status} onChange={(status) => setValues((v) => ({ ...v, status }))} />
            <Toggle label="管理员权限" value={values.is_admin} onChange={(is_admin) => setValues((v) => ({ ...v, is_admin }))} />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>取消</Button>
            <Button
              disabled={mutation.isPending || !values.username || (!row && !values.password)}
              onClick={() => mutation.mutate()}
            >
              保存用户
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

export function RedeemCodeGenerator({ onSaved }: { onSaved: () => void }) {
  const [open, setOpen] = useState(false)
  const [planId, setPlanId] = useState(0)
  const [count, setCount] = useState(1)
  const [expireAt, setExpireAt] = useState("")
  const plans = useQuery({
    queryKey: ["plans", true, "redeem"],
    queryFn: () => api<Entity[]>(withScope("plans", true)),
    enabled: open,
  })
  const mutation = useMutation({
    mutationFn: () => post("admin/redeem-codes/generate", { plan_id: planId, count, expire_at: expireAt.replace("T", " ") }),
    onSuccess: (r) => {
      toast.success(r.message)
      setOpen(false)
      onSaved()
    },
    onError: (e: Error) => toast.error(e.message),
  })
  return (
    <>
      <Button size="sm" onClick={() => setOpen(true)}>
        <Plus />生成兑换码
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>生成兑换码</DialogTitle>
            <DialogDescription>每次最多生成 200 个兑换码。</DialogDescription>
          </DialogHeader>
          <div className="grid gap-4">
            <div className="space-y-2">
              <Label>套餐</Label>
              <select
                className="h-8 w-full rounded-lg border bg-background px-2.5 text-sm"
                value={planId}
                onChange={(e) => setPlanId(Number(e.target.value))}
              >
                <option value={0}>请选择套餐</option>
                {(plans.data?.data ?? []).map((plan) => (
                  <option key={String(plan.id)} value={Number(plan.id)}>{String(plan.name)}</option>
                ))}
              </select>
            </div>
            <Field label="数量" type="number" value={String(count)} onChange={(v) => setCount(Number(v))} />
            <Field label="过期时间（可选）" type="datetime-local" value={expireAt} onChange={setExpireAt} />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>取消</Button>
            <Button disabled={!planId || count < 1 || count > 200 || mutation.isPending} onClick={() => mutation.mutate()}>
              生成
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

function Field({
  label,
  value,
  onChange,
  type = "text",
  placeholder,
  name,
  autoComplete = "off",
  ignorePasswordManager = false,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  type?: string
  placeholder?: string
  name?: string
  autoComplete?: string
  ignorePasswordManager?: boolean
}) {
  const id = useId()
  const isPassword = type === "password"
  const [showPassword, setShowPassword] = useState(false)
  const inputType = isPassword && showPassword ? "text" : type
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <div className="relative">
        <Input
          id={id}
          value={value}
          type={inputType}
          name={name}
          autoComplete={autoComplete}
          className={isPassword ? "pr-9" : undefined}
          data-form-type="other"
          data-1p-ignore={ignorePasswordManager ? "true" : undefined}
          data-lpignore={ignorePasswordManager ? "true" : undefined}
          data-bwignore={ignorePasswordManager ? "true" : undefined}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
        />
        {isPassword && (
          <button
            type="button"
            tabIndex={-1}
            aria-label={showPassword ? "隐藏密码" : "显示密码"}
            onClick={() => setShowPassword((s) => !s)}
            className="absolute right-1 top-1/2 -translate-y-1/2 cursor-pointer rounded p-0.5 text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50"
          >
            {showPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
          </button>
        )}
      </div>
    </div>
  )
}

function Toggle({
  label,
  hint,
  value,
  disabled = false,
  onChange,
}: {
  label: string
  hint?: string
  value: boolean
  disabled?: boolean
  onChange: (value: boolean) => void
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border p-3">
      <div className="min-w-0">
        <Label>{label}</Label>
        {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
      </div>
      <Switch checked={value} disabled={disabled} onCheckedChange={onChange} />
    </div>
  )
}
