import { useEffect, useRef, useState } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { toast } from "sonner"
import { ArrowRight, CheckCircle2, LoaderCircle, ShieldCheck } from "lucide-react"
import { post, setCsrfToken } from "@/lib/api"
import type { BootstrapData } from "@/types"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

const schema = z.object({ username: z.string().trim().min(3, "用户名至少 3 个字符"), password: z.string().min(10, "密码至少 10 个字符") })
type Values = z.infer<typeof schema>

export function LoginPage({ bootstrap, onAuthenticated }: { bootstrap: BootstrapData; onAuthenticated: () => Promise<void> }) {
  const [mode, setMode] = useState("login")
  const [turnstileToken, setTurnstileToken] = useState("")
  const [turnstileGeneration, setTurnstileGeneration] = useState(0)
  const { register, handleSubmit, formState: { errors, isSubmitting }, reset } = useForm<Values>({ resolver: zodResolver(schema) })
  const registrationEnabled = bootstrap.registration?.registration_enabled === true
  const turnstileEnabled = bootstrap.registration?.turnstile_enabled === true

  async function submit(values: Values) {
    if (turnstileEnabled && !turnstileToken) { toast.error("请先完成人机验证"); return }
    try {
      const response = await post<{ csrf_token?: string }>(`auth/${mode}`, {...values, "cf-turnstile-response": turnstileToken})
      setCsrfToken(response.data?.csrf_token)
      toast.success(response.message)
      if (mode === "register") { setMode("login"); reset({ username: values.username, password: "" }); return }
      await onAuthenticated()
      window.location.reload()
    } catch (error) {
      if (turnstileEnabled) {
        setTurnstileToken("")
        setTurnstileGeneration((value) => value + 1)
      }
      toast.error(error instanceof Error ? error.message : "操作失败")
    }
  }

  return <div className="grid min-h-svh lg:grid-cols-[1.05fr_.95fr]">
    <section className="relative hidden overflow-hidden bg-slate-950 p-12 text-white lg:flex lg:flex-col lg:justify-between">
      <div className="absolute inset-0 opacity-70 [background:radial-gradient(circle_at_20%_15%,rgba(13,148,136,.42),transparent_34%),radial-gradient(circle_at_85%_70%,rgba(14,116,144,.30),transparent_35%)]" />
      <div className="relative flex items-center gap-3"><span className="grid size-10 place-items-center rounded-xl bg-teal-400 text-slate-950"><ShieldCheck /></span><div><p className="font-semibold">{bootstrap.branding?.site_name || "AppleID 自动化管理"}</p><p className="text-sm text-slate-400">账号自动化管理平台</p></div></div>
      <div className="relative max-w-xl"><p className="mb-4 text-sm font-medium uppercase tracking-[.24em] text-teal-300">Operations, simplified</p><h1 className="text-4xl font-semibold leading-tight xl:text-5xl">让账号、任务与代理网络<br />始终处于可控状态。</h1><div className="mt-8 grid gap-4 text-sm text-slate-300"><p className="flex gap-3"><CheckCircle2 className="size-5 text-teal-300" />统一管理自动化执行与恢复流程</p><p className="flex gap-3"><CheckCircle2 className="size-5 text-teal-300" />实时掌握节点健康度与任务状态</p></div></div>
      <p className="relative text-xs text-slate-500">Secure workspace · Same-origin session protection</p>
    </section>
    <section className="flex items-center justify-center bg-muted/30 p-5"><Card className="w-full max-w-md border-border/70 shadow-xl shadow-slate-950/5"><CardHeader><CardTitle className="text-2xl"><h2>欢迎回来</h2></CardTitle><CardDescription>登录后进入您的业务工作台</CardDescription></CardHeader><CardContent>
      <Tabs value={mode} onValueChange={value => { setMode(value); setTurnstileToken("") }}><TabsList className="mb-6 grid w-full grid-cols-2"><TabsTrigger value="login">登录</TabsTrigger><TabsTrigger value="register" disabled={!registrationEnabled}>注册</TabsTrigger></TabsList>
        <TabsContent value={mode}><form onSubmit={handleSubmit(submit)} className="space-y-4">
          <div className="space-y-2"><Label htmlFor="username">用户名</Label><Input id="username" autoComplete="username" placeholder="请输入用户名" {...register("username")} />{errors.username && <p className="text-xs text-destructive">{errors.username.message}</p>}</div>
          <div className="space-y-2"><Label htmlFor="password">密码</Label><Input id="password" type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} placeholder="请输入密码" {...register("password")} />{errors.password && <p className="text-xs text-destructive">{errors.password.message}</p>}</div>
          {turnstileEnabled && <Turnstile key={`${mode}-${turnstileGeneration}`} action={mode} siteKey={String(bootstrap.registration.turnstile_site_key ?? "")} onVerify={setTurnstileToken} />}
          <Button type="submit" className="w-full" disabled={isSubmitting}>{isSubmitting ? <LoaderCircle className="animate-spin" /> : <ArrowRight />}{mode === "login" ? "进入工作台" : "创建账号"}</Button>
        </form></TabsContent>
      </Tabs>
    </CardContent></Card></section>
  </div>
}

declare global { interface Window { turnstile?: { render: (element: HTMLElement, options: Record<string, unknown>) => string; remove: (id: string) => void } } }

type TurnstileStatus = "loading" | "verified" | "error"

function Turnstile({siteKey,action,onVerify}:{siteKey:string;action:string;onVerify:(token:string)=>void}) {
  const widgetRef = useRef<HTMLDivElement>(null)
  const [attempt, setAttempt] = useState(0)
  const [status, setStatus] = useState<TurnstileStatus>("loading")

  useEffect(() => {
    let widget = ""
    let active = true
    let verified = false
    let script = document.querySelector<HTMLScriptElement>('script[data-react-turnstile]')

    const failed = () => {
      if (!active) return
      onVerify("")
      setStatus("error")
    }
    const render = () => {
      if (!active || !widgetRef.current || !window.turnstile || widget) return
      try {
        widget = window.turnstile.render(widgetRef.current, {
          sitekey: siteKey,
          action,
          callback: (token: string) => { if (active) { verified = true; onVerify(token); setStatus("verified") } },
          "expired-callback": failed,
          "error-callback": failed,
          "timeout-callback": failed,
        })
      } catch {
        failed()
      }
    }
    const scriptError = () => {
      if (script) script.dataset.loadFailed = "1"
      failed()
    }

    if (attempt > 0 && script && !window.turnstile) {
      script.remove()
      script = null
    }
    if (!script) {
      script = document.createElement("script")
      script.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit"
      script.async = true
      script.defer = true
      script.dataset.reactTurnstile = "1"
      document.head.appendChild(script)
    }
    script.addEventListener("load", render)
    script.addEventListener("error", scriptError)
    render()
    const timer = window.setTimeout(() => {
      if (!verified) failed()
    }, 10_000)

    return () => {
      active = false
      window.clearTimeout(timer)
      script?.removeEventListener("load", render)
      script?.removeEventListener("error", scriptError)
      if (widget) window.turnstile?.remove(widget)
    }
  }, [siteKey, action, onVerify, attempt])

  const retry = () => {
    setStatus("loading")
    setAttempt(value => value + 1)
  }

  return <div className="space-y-2" aria-label="人机验证">
    <div ref={widgetRef} className="min-h-16" />
    {status === "loading" && <p className="flex items-center gap-2 text-xs text-muted-foreground" role="status"><LoaderCircle className="size-4 animate-spin" />正在加载或等待人机验证…</p>}
    {status === "error" && <div className="flex items-center justify-between gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2"><p className="text-xs text-amber-700 dark:text-amber-300">如果上方未显示验证组件，请重新加载。</p><Button type="button" size="sm" variant="outline" onClick={retry}>重新加载</Button></div>}
  </div>
}
