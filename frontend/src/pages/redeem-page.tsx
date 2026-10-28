import { useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { Gift, LoaderCircle } from "lucide-react"
import { toast } from "sonner"
import { post } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

export function RedeemPage(){const[code,setCode]=useState("");const mutation=useMutation({mutationFn:()=>post("redeem",{code}),onSuccess:r=>{toast.success(r.message);setCode("")},onError:(e:Error)=>toast.error(e.message)});return <div className="mx-auto max-w-xl space-y-5"><div><h2 className="text-2xl font-semibold">兑换码</h2><p className="mt-1 text-sm text-muted-foreground">输入有效兑换码，将订阅添加至当前账号。</p></div><Card><CardHeader><div className="mb-2 grid size-10 place-items-center rounded-lg bg-primary/10 text-primary"><Gift/></div><CardTitle>兑换服务权益</CardTitle><CardDescription>兑换成功后，新的账号和分享页配额会立即生效。</CardDescription></CardHeader><CardContent><form className="space-y-4" onSubmit={e=>{e.preventDefault();mutation.mutate()}}><div className="space-y-2"><Label htmlFor="redeem-code">兑换码</Label><Input id="redeem-code" value={code} onChange={e=>setCode(e.target.value)} placeholder="请输入兑换码" autoComplete="off"/></div><Button type="submit" disabled={!code.trim()||mutation.isPending}>{mutation.isPending?<LoaderCircle className="animate-spin"/>:<Gift/>}立即兑换</Button></form></CardContent></Card></div>}
