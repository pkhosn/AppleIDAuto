import { useEffect, useState } from "react"
import { useMutation, useQuery } from "@tanstack/react-query"
import { z } from "zod"
import { toast } from "sonner"
import { api, post, withScope } from "@/lib/api"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"

export const cycleDefinitions = [
  ["password_check", "检测账号密码正确性", "enable_check_password_correct", 30],
  ["two_factor", "自动解除双重认证", "enable_auto_disable_two_factor", 30],
  ["profile_restore", "自动恢复资料", "enable_auto_restore_profile", 360],
  ["device_cleanup", "自动移除设备", "enable_delete_devices", 30],
  ["password_update", "自动更新密码", "enable_auto_update_password", 1440],
] as const
export type CycleKey = typeof cycleDefinitions[number][0]
export type Cycle = { source: "global" | "custom"; minutes: number }
export type Cycles = Record<CycleKey, Cycle>
export type SharedCycle = { enabled: boolean; minutes: number; features: CycleKey[] }
export const defaultSharedCycle: SharedCycle = { enabled: false, minutes: 30, features: [] }
export function validSharedCycle(shared: SharedCycle) { return minuteSchema.safeParse(shared.minutes).success && (!shared.enabled || shared.features.length > 0) }
export type Globals = { active: boolean; features: Record<CycleKey, number> }
export const defaultCycles = Object.fromEntries(cycleDefinitions.map(([key, , , minutes]) => [key, { source: "global", minutes }])) as Cycles
export const minuteSchema = z.number().int().min(1).max(43200)
export function validCycles(cycles: Cycles) { return Object.values(cycles).every(c => minuteSchema.safeParse(c.minutes).success) }
export function formatMinutes(minutes: number) {
  return minutes % 1440 === 0 ? `${minutes / 1440} 天` : minutes % 60 === 0 ? `${minutes / 60} 小时` : `${minutes} 分钟`
}

export function IntervalInput({ minutes, onChange, disabled, label }: { minutes: number; onChange: (minutes: number) => void; disabled?: boolean; label: string }) {
  const [unit, setUnit] = useState(minutes % 1440 === 0 ? 1440 : minutes % 60 === 0 ? 60 : 1)
  return <div className="flex gap-2">
    <Input aria-label={`${label}周期数值`} type="number" min={1 / unit} max={43200 / unit} step="any" value={Number.isFinite(minutes) ? minutes / unit : ""} disabled={disabled}
      aria-invalid={!minuteSchema.safeParse(minutes).success} onChange={e => onChange(e.target.value === "" ? NaN : Number(e.target.value) * unit)} />
    <Select value={String(unit)} onValueChange={v => setUnit(Number(v))} disabled={disabled}>
      <SelectTrigger aria-label={`${label}时间单位`}><SelectValue>{unit === 1440 ? "天" : unit === 60 ? "小时" : "分钟"}</SelectValue></SelectTrigger>
      <SelectContent>{[[1, "分钟"], [60, "小时"], [1440, "天"]].map(([value, text]) => <SelectItem key={value} value={String(value)}>{text}</SelectItem>)}</SelectContent>
    </Select>
  </div>
}

type FeatureState = { result: string; last_attempt_at?: string; last_finished_at?: string; next_run_at?: string; message?: string; reconcile_at?: string; reconcile_attempts?: number }
type RunStep = { feature: CycleKey; status: string; stage?: string; error_code?: string; message?: string; duration_ms?: number }
type TaskRun = {
  run_id: string; status: string; stage: string; error_code?: string; retry_decision?: string; current_feature?: CycleKey;
  queued_at: string; started_at?: string; finished_at?: string; heartbeat_at?: string; steps?: RunStep[]
}
type State = { features: Partial<Record<CycleKey, FeatureState>>; status: string; due: CycleKey[]; active_run?: TaskRun | null; runs?: TaskRun[] }
const time = (value?: string) => value ? new Date(value).toLocaleString() : "—"
const resultLabels: Record<string, string> = { never: "尚未执行", running: "执行中", success: "成功", failed: "失败", blocked: "前置条件未满足", uncertain: "结果待人工确认", cancelled: "计划已取消或调整" }
const stageLabels: Record<string, string> = { queued: "排队", preflight: "预检", login: "登录", authorization: "执行前授权", mutation_submitted: "已提交修改", result_confirmation: "结果确认", reporting: "主站回写", reconciling: "只读核验", completed: "完成", interrupted: "已中断", lock_lost: "账号锁失效" }
const retryLabels: Record<string, string> = { none: "无需重试", safe_retry: "可安全重试", reconcile: "等待只读核验", writeback: "仅重试主站回写", manual: "等待人工确认" }
const featureLabels = Object.fromEntries(cycleDefinitions.map(([key, label]) => [key, label])) as Record<CycleKey, string>
const elapsed = (start?: string, end?: string) => {
  if (!start) return "—"
  const milliseconds = Math.max(new Date(end ?? Date.now()).getTime() - new Date(start).getTime(), 0)
  return milliseconds < 1000 ? `${milliseconds} ms` : `${Math.round(milliseconds / 100) / 10} 秒`
}

export function AccountCycles({ cycles, onChange, shared = defaultSharedCycle, onSharedChange = () => undefined, flags, setFlag, accountId, admin, open }: {
  cycles: Cycles; onChange: (cycles: Cycles) => void; flags: Record<string, unknown>; setFlag: (flag: string, value: boolean) => void;
  shared?: SharedCycle; onSharedChange?: (shared: SharedCycle) => void;
  accountId?: number | string; admin: boolean; open: boolean
}) {
  const defaults = useQuery({ queryKey: ["automation-defaults"], queryFn: () => api<Globals>("automation-defaults"), enabled: open, refetchInterval: 30000 })
  const status = useQuery({ queryKey: ["feature-status", accountId, admin], queryFn: () => api<State>(withScope(`accounts/${accountId}/schedule`, admin)), enabled: open && !!accountId && !!defaults.data?.data.active, refetchInterval: 10000 })
  const runFeature = useMutation({
    mutationFn: ({ feature, mode, confirmation }: { feature: CycleKey; mode: "feature" | "reconcile"; confirmation?: string }) => post(withScope(`tasks/${accountId}/restart`, admin), { feature, mode, confirmation }),
    onSuccess: response => { toast.success(response.message); status.refetch() },
    onError: (error: Error) => toast.error(error.message),
  })
  return <section className="space-y-3 sm:col-span-2" aria-label="独立自动化周期">
    <div><h3 className="font-medium">独立自动化周期</h3><p className="text-xs text-muted-foreground">勾选的任务使用账号共用周期，其余保留单项周期。任务启停独立设置。</p></div>
    <div className="space-y-3 rounded-md border p-3">
      <div className="flex flex-wrap items-center gap-3">
        <Label htmlFor="shared-cycle">共用任务周期</Label>
        <Switch id="shared-cycle" aria-label="共用任务周期" checked={shared.enabled} onCheckedChange={enabled => onSharedChange({ ...shared, enabled })} />
        <IntervalInput label="账号共用" minutes={shared.minutes} onChange={minutes => onSharedChange({ ...shared, minutes })} />
      </div>
      <div className="flex flex-wrap gap-3">{cycleDefinitions.map(([key, label]) => <label key={key} className="flex items-center gap-2 text-sm">
        <input type="checkbox" aria-label={`${label}加入共用周期`} checked={shared.features.includes(key)} onChange={e => onSharedChange({ ...shared,
          features: e.target.checked ? [...shared.features, key] : shared.features.filter(item => item !== key) })} />{label}
      </label>)}</div>
      <p className="text-xs text-muted-foreground">仅影响当前账号。勾选不会启用任务；关闭共用周期会恢复各项原有设置。</p>
      {!validSharedCycle(shared) && <p role="alert" className="text-xs text-destructive">请输入 1 分钟至 30 天的整数分钟周期，开启时至少勾选一项任务。</p>}
    </div>
    {!defaults.data?.data.active && <p className="text-sm text-amber-600">独立调度尚未切换；当前仍按旧检查间隔执行。</p>}
    {defaults.error && <p role="alert">全局周期加载失败，请重试。</p>}
    {status.error && <p role="alert">执行状态暂不可用，配置不受影响。</p>}
    <div className="grid gap-3 sm:grid-cols-2">{cycleDefinitions.map(([key, label, flag, fallback]) => {
      const cycle = cycles[key], state = status.data?.data.features?.[key]
      const global = defaults.data?.data.features[key] ?? fallback
      const enabled = Boolean(flags[flag])
      const usesShared = shared.enabled && shared.features.includes(key)
      const effective = usesShared ? shared.minutes : cycle.source === "global" ? global : cycle.minutes
      return <Card key={key} className="gap-3 py-4"><CardHeader className="px-4"><CardTitle className="flex items-center justify-between gap-2 text-sm">{label}<Switch aria-label={`启用${label}`} checked={enabled} onCheckedChange={v => setFlag(flag, v)} /></CardTitle></CardHeader>
        <CardContent className="space-y-3 px-4">
          {!usesShared && <IntervalInput key={`${key}-${cycle.source}`} label={label} minutes={effective} onChange={minutes => onChange({ ...cycles, [key]: { ...cycle, source: "custom", minutes } })} />}
          <p className="text-xs text-muted-foreground">{usesShared ? "账号共用周期" : cycle.source === "global" ? "网站默认周期（编辑后改为单项自定义）" : "单项自定义"} · 生效周期：{formatMinutes(effective)}{!enabled ? "（已关闭，不调度）" : ""}</p>
          {key === "device_cleanup" && <p className="text-xs text-amber-600">开启后每轮会移除该 Apple 账号设备页中的全部设备；列表为空时按成功记录。</p>}
          {!minuteSchema.safeParse(cycle.minutes).success && <p className="text-xs text-destructive" role="alert">请输入 1 分钟至 30 天，换算后须为整数分钟。</p>}
          <dl className="space-y-1 text-xs text-muted-foreground">
            <div>调度状态：{!enabled ? "功能已关闭" : state?.result === "uncertain" ? state.reconcile_at ? "等待核验" : (state.reconcile_attempts ?? 0) >= 2 ? "核验重试耗尽" : "等待安排核验" : state?.result === "running" ? "执行中" : "等待下次执行"}</div>
            {enabled && state?.reconcile_at && <div>核验时间：{time(state.reconcile_at)}</div>}
            <div>最近结果：{resultLabels[state?.result ?? "never"] ?? state?.result}{status.data?.data.status === "pending" && status.data.data.due?.includes(key) ? " · 等待执行" : ""}</div>
            <div>上次尝试：{time(state?.last_attempt_at)}</div><div>上次结束：{time(state?.last_finished_at)}</div>
            <div>下次计划：{enabled ? time(state?.next_run_at) : "已取消"}</div>
          </dl>
          {state?.message && <p className="break-words text-xs">{state.message}</p>}
          {!!accountId && enabled && state?.result === "uncertain" && <div className="flex flex-wrap gap-2"><Button type="button" size="sm" variant="outline" disabled={runFeature.isPending}
            onClick={() => runFeature.mutate({ feature: key, mode: "reconcile" })}>先只读核验</Button>
            {(["profile_restore", "device_cleanup", "password_update"] as CycleKey[]).includes(key) && <Button type="button" size="sm" variant="destructive" disabled={runFeature.isPending}
              onClick={() => window.confirm(`再次提交${label}可能重复执行不可逆操作。请确认你已人工核对 Apple 当前状态，并仍要继续。`) && runFeature.mutate({ feature: key, mode: "feature", confirmation: "CONFIRM_UNCERTAIN_RETRY" })}>人工确认后重新提交</Button>}</div>}
          {!!accountId && enabled && state?.result !== "uncertain" && <Button type="button" size="sm" variant="outline" disabled={runFeature.isPending}
            onClick={() => runFeature.mutate({ feature: key, mode: "feature" })}>单独运行此项</Button>}
        </CardContent></Card>
    })}</div>
    {!!accountId && defaults.data?.data.active && <Card><CardHeader><CardTitle className="text-sm">自动化运行时间线</CardTitle>
      <CardDescription>排队时间不计入浏览器执行超时；破坏性操作提交后只会核验，不会自动重放。</CardDescription></CardHeader>
      <CardContent className="space-y-3">
        {!status.data?.data.runs?.length && <p className="text-xs text-muted-foreground">暂无运行记录。</p>}
        {status.data?.data.runs?.slice(0, 5).map(run => <div key={run.run_id} className="space-y-2 rounded-lg border p-3 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-2"><span className="font-medium">运行 {run.run_id.slice(0, 8)}</span><span>{resultLabels[run.status] ?? stageLabels[run.stage] ?? run.status}</span></div>
          <div className="grid gap-1 text-muted-foreground sm:grid-cols-2">
            <span>阶段：{stageLabels[run.stage] ?? run.stage}</span><span>当前功能：{run.current_feature ? featureLabels[run.current_feature] : "—"}</span>
            <span>排队：{elapsed(run.queued_at, run.started_at)}</span><span>执行：{elapsed(run.started_at, run.finished_at)}</span>
            <span>错误码：{run.error_code || "—"}</span><span>处理：{retryLabels[run.retry_decision ?? ""] ?? run.retry_decision ?? "—"}</span>
          </div>
          {!!run.steps?.length && <div className="space-y-1 border-t pt-2">{run.steps.map(step => <div key={step.feature} className="flex flex-wrap justify-between gap-2">
            <span>{featureLabels[step.feature]} · {stageLabels[step.stage ?? ""] ?? step.stage ?? "—"}</span>
            <span>{resultLabels[step.status] ?? step.status}{step.error_code ? ` · ${step.error_code}` : ""}{step.duration_ms !== undefined ? ` · ${elapsed(new Date(0).toISOString(), new Date(step.duration_ms).toISOString())}` : ""}</span>
          </div>)}</div>}
        </div>)}
      </CardContent>
    </Card>}
  </section>
}

export function GlobalCycles() {
  const query = useQuery({ queryKey: ["automation-defaults"], queryFn: () => api<Globals>("automation-defaults") })
  const [values, setValues] = useState<Record<CycleKey, number> | null>(null)
  useEffect(() => { if (query.data) setValues(query.data.data.features) }, [query.data])
  const save = useMutation({ mutationFn: () => post("admin/settings/automation", { features: values }), onSuccess: r => { toast.success(r.message); query.refetch() }, onError: (e: Error) => toast.error(e.message) })
  return <Card><CardHeader><CardTitle>自动化周期</CardTitle><CardDescription>仅影响跟随全局的账号，60 秒内同步。已有账号的自定义周期不变。</CardDescription></CardHeader><CardContent className="space-y-4">
    {query.error && <p role="alert">配置加载失败</p>}
    {values && cycleDefinitions.map(([key, label]) => <div className="space-y-2" key={key}><p className="text-sm">{label}</p><IntervalInput label={`全局${label}`} minutes={values[key]} onChange={minutes => setValues({ ...values, [key]: minutes })} /></div>)}
    <p className="text-xs text-muted-foreground">支持 1 分钟至 30 天；单位切换仅换算显示，不改变周期。</p>
    <Button disabled={!values || save.isPending || !Object.values(values).every(v => minuteSchema.safeParse(v).success)} onClick={() => save.mutate()}>保存全局周期</Button>
  </CardContent></Card>
}
