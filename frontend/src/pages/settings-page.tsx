import { useEffect } from "react"
import { useForm } from "react-hook-form"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CreditCard, LoaderCircle, Save, ShieldAlert } from "lucide-react"
import { toast } from "sonner"
import { api, post } from "@/lib/api"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { ErrorState, PageSkeleton } from "@/components/page-state"

type Settings = { branding: Record<string, unknown>; registration: Record<string, unknown>; payment: Record<string, unknown> }
type BrandingValues = { site_name: string; site_description: string; site_logo: string; admin_path: string; share_watermark_enabled: boolean; share_watermark_text: string }
type SecurityValues = { registration_enabled: boolean; turnstile_enabled: boolean; turnstile_site_key: string; turnstile_secret_key: string; turnstile_hostname: string }
type PaymentValues = { gateway: string; pid: string; merchant_key: string; status: boolean }

export function SettingsPage({ section }: { section: "branding" | "registration" | "payment" }) {
  const query = useQuery({ queryKey: ["settings"], queryFn: () => api<Settings>("admin/settings") })
  const client = useQueryClient()
  const branding = useForm<BrandingValues>({ defaultValues: { site_name: "", site_description: "", site_logo: "", admin_path: "admin", share_watermark_enabled: true, share_watermark_text: "⚠️禁止在【设置】登录" } })
  const security = useForm<SecurityValues>({ defaultValues: { registration_enabled: false, turnstile_enabled: false, turnstile_site_key: "", turnstile_secret_key: "", turnstile_hostname: "" } })
  const payment = useForm<PaymentValues>({ defaultValues: { gateway: "", pid: "", merchant_key: "", status: false } })
  useEffect(() => {
    if (!query.data) return
    const b = query.data.data.branding, r = query.data.data.registration, p = query.data.data.payment ?? {}
    branding.reset({ site_name: String(b.site_name ?? ""), site_description: String(b.site_description ?? ""), site_logo: String(b.site_logo ?? ""), admin_path: String(b.admin_path ?? "admin"), share_watermark_enabled: Boolean(b.share_watermark_enabled ?? true), share_watermark_text: String(b.share_watermark_text ?? "⚠️禁止在【设置】登录") })
    security.reset({ registration_enabled: Boolean(r.registration_enabled), turnstile_enabled: Boolean(r.turnstile_enabled), turnstile_site_key: String(r.turnstile_site_key ?? ""), turnstile_secret_key: "", turnstile_hostname: String(r.turnstile_hostname ?? "") })
    payment.reset({ gateway: String(p.gateway ?? ""), pid: String(p.pid ?? ""), merchant_key: "", status: Boolean(p.status ?? false) })
  }, [query.data, branding, security, payment])
  const mutation = useMutation({
    mutationFn: (payload: BrandingValues | SecurityValues | PaymentValues) => post(`admin/settings/${section}`, payload),
    onSuccess: (r, payload) => {
      toast.success(r.message)
      client.invalidateQueries({ queryKey: ["settings"] })
      client.invalidateQueries({ queryKey: ["bootstrap"] })
      if (section === "branding" && !window.location.pathname.startsWith("/react-preview")) {
        const next = (payload as BrandingValues).admin_path
        const previous = String(query.data?.data.branding.admin_path ?? "")
        if (next && next !== previous) window.location.assign(`/${next}/site`)
      }
    },
    onError: (e: Error) => toast.error(e.message),
  })
  if (query.isLoading) return <PageSkeleton />
  if (query.error) return <ErrorState error={query.error} retry={() => query.refetch()} />

  if (section === "branding") {
    return (
      <div className="max-w-3xl space-y-5">
        <div><h2 className="text-2xl font-semibold">品牌与站点</h2><p className="mt-1 text-sm text-muted-foreground">统一配置品牌展示与动态管理入口。</p></div>
        <form onSubmit={branding.handleSubmit((v) => mutation.mutate(v))}>
          <Card>
            <CardHeader><CardTitle>站点识别</CardTitle><CardDescription>管理路径变更会同步改变当前 React 管理端入口，请妥善记录。</CardDescription></CardHeader>
            <CardContent className="grid gap-5">
              {[["site_name", "站点名称", "AppleID 自动化管理"], ["site_description", "站点描述", "统一管理账号、任务与代理网络"], ["site_logo", "Logo 地址", "https://example.com/logo.svg"], ["admin_path", "管理端路径", "admin"]].map(([key, label, placeholder]) => (
                <div className="space-y-2" key={key}><Label htmlFor={key}>{label}</Label><Input id={key} placeholder={placeholder} {...branding.register(key as keyof BrandingValues, { required: key === "site_name" || key === "admin_path" })} /></div>
              ))}
              <div className="space-y-4 rounded-lg border p-4">
                <h3 className="font-medium">分享页水印</h3>
                <p className="text-sm text-muted-foreground">普通分享页与 iframe 账号卡片共用以下设置。</p>
                <Toggle label="启用水印" checked={branding.watch("share_watermark_enabled")} onCheckedChange={v => branding.setValue("share_watermark_enabled", v, { shouldDirty: true })} />
                <div className="space-y-2"><Label htmlFor="share-watermark-text">水印文字</Label><Input id="share-watermark-text" maxLength={40} {...branding.register("share_watermark_text", { validate: value => !branding.getValues("share_watermark_enabled") || Boolean(value.trim()) })} /><p className="text-xs text-muted-foreground">最多 40 个字符；启用时不能为空。</p></div>
              </div>
              <Button type="submit" className="w-fit" disabled={mutation.isPending}>{mutation.isPending ? <LoaderCircle className="animate-spin" /> : <Save />}保存站点设置</Button>
            </CardContent>
          </Card>
        </form>
      </div>
    )
  }

  if (section === "payment") {
    const keyConfigured = Boolean(query.data?.data.payment?.merchant_key_configured)
    return (
      <div className="max-w-3xl space-y-5">
        <div><h2 className="text-2xl font-semibold">支付配置</h2><p className="mt-1 text-sm text-muted-foreground">对接易支付网关后，用户才可购买套餐。</p></div>
        <Alert><CreditCard /><AlertTitle>易支付接口</AlertTitle><AlertDescription>商户密钥仅保存不回显；留空提交表示保持不变{keyConfigured ? "（当前已配置）" : ""}。</AlertDescription></Alert>
        <form onSubmit={payment.handleSubmit((v) => mutation.mutate(v))}>
          <Card>
            <CardHeader><CardTitle>网关与商户</CardTitle><CardDescription>启用后，套餐购买将跳转到此网关完成收款。</CardDescription></CardHeader>
            <CardContent className="grid gap-5">
              <div className="space-y-2"><Label htmlFor="payment-gateway">提交网关地址</Label><Input id="payment-gateway" placeholder="https://payment.example.com/submit.php" {...payment.register("gateway", { required: true })} /></div>
              <div className="space-y-2"><Label htmlFor="payment-pid">商户 ID</Label><Input id="payment-pid" placeholder="易支付商户号" {...payment.register("pid", { required: true })} /></div>
              <div className="space-y-2"><Label htmlFor="payment-key">商户密钥</Label><Input id="payment-key" type="password" autoComplete="new-password" placeholder={keyConfigured ? "已配置，留空保持不变" : "易支付商户密钥"} {...payment.register("merchant_key")} /></div>
              <div className="flex items-center justify-between rounded-lg border p-3"><Label htmlFor="payment-status">启用支付</Label><Switch id="payment-status" checked={payment.watch("status")} onCheckedChange={(v) => payment.setValue("status", v, { shouldDirty: true })} /></div>
              <Button type="submit" className="w-fit" disabled={mutation.isPending}>{mutation.isPending ? <LoaderCircle className="animate-spin" /> : <Save />}保存支付配置</Button>
            </CardContent>
          </Card>
        </form>
      </div>
    )
  }

  return (
    <div className="max-w-3xl space-y-5">
      <div><h2 className="text-2xl font-semibold">访问安全</h2><p className="mt-1 text-sm text-muted-foreground">控制登录、公开注册与 Cloudflare Turnstile 人机验证。</p></div>
      <Alert><ShieldAlert /><AlertTitle>安全约束</AlertTitle><AlertDescription>开放注册前必须同时启用并完整配置 Turnstile。Secret Key 留空时保持原值。</AlertDescription></Alert>
      <form onSubmit={security.handleSubmit((v) => mutation.mutate(v))}>
        <Card>
          <CardHeader><CardTitle>注册与人机验证</CardTitle></CardHeader>
          <CardContent className="grid gap-5">
            <Toggle label="开放用户注册" checked={security.watch("registration_enabled")} onCheckedChange={(v) => security.setValue("registration_enabled", v, { shouldDirty: true })} />
            <Toggle label="登录和注册启用 Cloudflare Turnstile" checked={security.watch("turnstile_enabled")} onCheckedChange={(v) => security.setValue("turnstile_enabled", v, { shouldDirty: true })} />
            {[["turnstile_site_key", "Site Key", "Cloudflare Turnstile Site Key"], ["turnstile_secret_key", "Secret Key", "已配置时留空可保持不变"], ["turnstile_hostname", "允许域名", "apple.example.com,www.example.com"]].map(([key, label, placeholder]) => (
              <div className="space-y-2" key={key}>
                <Label htmlFor={key}>{label}</Label>
                <Input id={key} type={key.includes("secret") ? "password" : "text"} placeholder={placeholder} {...security.register(key as keyof SecurityValues)} />
                {key === "turnstile_hostname" && <p className="text-xs text-muted-foreground">可填写多个域名，请使用英文逗号（,）分隔；留空则不限制域名。</p>}
              </div>
            ))}
            <Button type="submit" className="w-fit" disabled={mutation.isPending}>{mutation.isPending ? <LoaderCircle className="animate-spin" /> : <Save />}保存安全配置</Button>
          </CardContent>
        </Card>
      </form>
    </div>
  )
}

function Toggle({ label, checked, onCheckedChange }: { label: string; checked: boolean; onCheckedChange: (value: boolean) => void }) {
  return <div className="flex items-center justify-between rounded-lg border p-3"><Label>{label}</Label><Switch checked={checked} onCheckedChange={onCheckedChange} /></div>
}
