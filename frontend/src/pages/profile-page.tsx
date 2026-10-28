import { useEffect } from "react"
import { useForm } from "react-hook-form"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { LoaderCircle, Save } from "lucide-react"
import { toast } from "sonner"
import { api, post } from "@/lib/api"
import type { Entity } from "@/types"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ErrorState, PageSkeleton } from "@/components/page-state"

type Values = { username: string; password: string }
const fields: [keyof Values, string, string, string?][] = [["username","用户名","您的登录用户名"],["password","新密码","不修改请留空","password"]]

export function ProfilePage() {
  const query = useQuery({ queryKey: ["profile"], queryFn: () => api<Entity>("profile") })
  const client = useQueryClient()
  const form = useForm<Values>({ defaultValues: { username:"",password:"" } })
  useEffect(() => { if (query.data?.data) form.reset({ username:String(query.data.data.username??""), password:"" }) }, [query.data, form])
  useEffect(() => { const guard=(e:BeforeUnloadEvent)=>{if(form.formState.isDirty)e.preventDefault()}; addEventListener("beforeunload",guard); return()=>removeEventListener("beforeunload",guard)}, [form.formState.isDirty])
  const mutation = useMutation({ mutationFn: (values: Values) => post<Entity>("profile", values), onSuccess: r => { toast.success(r.message); form.reset({...form.getValues(),password:""}); client.invalidateQueries({queryKey:["bootstrap"]}) }, onError:(e:Error)=>toast.error(e.message) })
  if (query.isLoading) return <PageSkeleton />
  if (query.error) return <ErrorState error={query.error} retry={() => query.refetch()} />
  return <div className="max-w-3xl space-y-5"><div><h2 className="text-2xl font-semibold">个人设置</h2><p className="mt-1 text-sm text-muted-foreground">维护登录资料。留空的新密码不会覆盖当前密码。</p></div><form onSubmit={form.handleSubmit(v=>mutation.mutate(v))}><Card><CardHeader><CardTitle>账号资料</CardTitle><CardDescription>修改登录用户名或密码。</CardDescription></CardHeader><CardContent className="grid gap-5 sm:grid-cols-2">{fields.map(([key,label,placeholder,type])=><div className="space-y-2" key={key}><Label htmlFor={key}>{label}</Label><Input id={key} type={type} placeholder={placeholder} autoComplete={type==="password"?"new-password":"off"} {...form.register(key,{required:key==="username"})} /></div>)}<div className="sm:col-span-2"><Button type="submit" disabled={mutation.isPending||!form.formState.isDirty}>{mutation.isPending?<LoaderCircle className="animate-spin"/>:<Save/>}保存更改</Button></div></CardContent></Card></form></div>
}
