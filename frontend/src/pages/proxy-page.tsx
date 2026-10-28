import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { Activity, DatabaseZap, Pencil, Plus, Server, Trash2 } from "lucide-react"
import { toast } from "sonner"
import { api, post, withScope } from "@/lib/api"
import type { BootstrapData, Entity } from "@/types"
import { Button } from "@/components/ui/button"
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from "@/components/ui/alert-dialog"
import { EntityTable, statusNode } from "@/components/entity-table"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { ErrorState, PageSkeleton } from "@/components/page-state"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Pagination, PaginationContent, PaginationItem, PaginationNext, PaginationPrevious } from "@/components/ui/pagination"
import { PerPageSelect, usePerPage } from "@/components/per-page-select"

type ProxyData = { nodes: Entity[]; sources: Entity[]; protocols: unknown[] }
export function ProxyPage({ admin = false }: { admin?: boolean }) {
  const client = useQueryClient()
  const [nodePage, setNodePage] = useState(1)
  const [nodePerPage, setNodePerPage] = usePerPage("proxy-static")
  const proxiesParams = new URLSearchParams({ page: String(nodePage), per_page: String(nodePerPage) })
  const proxies = useQuery({ queryKey: ["proxies", admin, nodePage, nodePerPage], queryFn: () => api<ProxyData>(withScope(`proxies?${proxiesParams.toString()}`, admin)) })
  const [testingStaticId, setTestingStaticId] = useState<string | number | null>(null)
  const nodePages = proxies.data?.meta?.total_pages ?? proxies.data?.meta?.last_page ?? 1
  const testStatic = useMutation({ mutationFn: (id: string | number) => post(withScope(`proxies/${id}/test`, admin)), onMutate: id => setTestingStaticId(id), onSettled: () => setTestingStaticId(null), onSuccess: r => { toast.success(r.message); client.invalidateQueries({ queryKey: ["proxies", admin] }) }, onError: (e: Error) => { toast.error(e.message); client.invalidateQueries({ queryKey: ["proxies", admin] }) } })
  const remove = useMutation({mutationFn:({type,id}:{type:"proxy"|"source";id:string|number})=>api(`${type==="proxy"?"proxies":"proxy-sources"}/${id}${admin?"?scope=admin":""}`,{method:"DELETE"}),onSuccess:r=>{toast.success(r.message);client.invalidateQueries({queryKey:["proxies",admin]})},onError:(e:Error)=>toast.error(e.message)})
  if (proxies.isLoading) return <PageSkeleton />
  if (proxies.error) return <ErrorState error={proxies.error} retry={() => proxies.refetch()} />
  const changeNodePerPage = (next: number) => { setNodePerPage(next); setNodePage(1) }
  return <div className="space-y-5">
    <div className="flex flex-wrap items-end justify-between gap-3"><div><h2 className="text-2xl font-semibold tracking-tight">代理网络</h2><p className="mt-1 text-sm text-muted-foreground">管理静态 IP 和自动提取源。</p></div><ProxyEditor admin={admin} onSaved={()=>client.invalidateQueries({queryKey:["proxies",admin]})}/></div>
    <div className="grid gap-3 sm:grid-cols-2"><Card size="sm"><CardHeader><CardDescription>静态 IP</CardDescription><CardTitle className="text-2xl">{proxies.data?.meta?.total ?? proxies.data?.data.nodes.length ?? 0}</CardTitle></CardHeader></Card><Card size="sm"><CardHeader><CardDescription>自动提取源</CardDescription><CardTitle className="text-2xl">{proxies.data?.data.sources.length ?? 0}</CardTitle></CardHeader></Card></div>
    <Tabs defaultValue="fixed"><TabsList className="w-full justify-start overflow-x-auto sm:w-auto"><TabsTrigger value="fixed"><Server />静态 IP</TabsTrigger><TabsTrigger value="sources"><DatabaseZap />自动提取源</TabsTrigger></TabsList>
      <TabsContent value="fixed" className="pt-3"><EntityTable rows={proxies.data?.data.nodes ?? []} columns={[{key:"name",label:"节点"},{key:"protocol",label:"协议"},{key:"content",label:"连接地址"},{key:"health_status",label:"健康状态",render:statusNode},{key:"exit_ip",label:"出口 IP"},{key:"latency_ms",label:"检测耗时(ms)"},{key:"bound_count",label:"已绑定"}]} actions={row=><div className="flex justify-end gap-1"><Button size="sm" variant="outline" disabled={testStatic.isPending && testingStaticId === row.id} onClick={()=>testStatic.mutate(row.id!)}><Activity/>{testStatic.isPending && testingStaticId === row.id?"测试中…":"测试"}</Button><ProxyEditor admin={admin} row={row} onSaved={()=>client.invalidateQueries({queryKey:["proxies",admin]})}/><DeleteButton title="删除静态 IP？" onConfirm={()=>remove.mutate({type:"proxy",id:row.id!})}/></div>} /><div className="flex flex-wrap items-center justify-between gap-3 pt-3"><p className="text-sm text-muted-foreground">共 {proxies.data?.meta?.total ?? 0} 条</p><PerPageSelect value={nodePerPage} onChange={changeNodePerPage}/></div>{nodePages>1&&<Pagination><PaginationContent><PaginationItem><PaginationPrevious href="#" text="上一页" aria-disabled={nodePage<=1} onClick={e=>{e.preventDefault();if(nodePage>1)setNodePage(nodePage-1)}}/></PaginationItem><PaginationItem><span className="px-3 text-sm text-muted-foreground">{nodePage} / {nodePages}</span></PaginationItem><PaginationItem><PaginationNext href="#" text="下一页" aria-disabled={nodePage>=nodePages} onClick={e=>{e.preventDefault();if(nodePage<nodePages)setNodePage(nodePage+1)}}/></PaginationItem></PaginationContent></Pagination>}</TabsContent>
      <TabsContent value="sources" className="pt-3"><div className="mb-3 flex justify-end"><ProxyEditor source admin={admin} onSaved={()=>client.invalidateQueries({queryKey:["proxies",admin]})}/></div><EntityTable rows={proxies.data?.data.sources ?? []} columns={[{key:"name",label:"来源"},{key:"protocol",label:"协议"},{key:"refresh_interval",label:"同步周期(秒)"},{key:"status",label:"状态",render:statusNode},{key:"last_fetch_at",label:"最近同步"},{key:"last_error",label:"异常信息"}]} actions={row=><div className="flex justify-end gap-1"><ProxyEditor source admin={admin} row={row} onSaved={()=>client.invalidateQueries({queryKey:["proxies",admin]})}/><DeleteButton title="删除自动提取源？" onConfirm={()=>remove.mutate({type:"source",id:row.id!})}/></div>} /></TabsContent>
    </Tabs>
  </div>
}

type EditorValues={name:string;protocol:string;content:string;source_url:string;max_accounts:number;refresh_interval:number;status:boolean;owner:number}
const defaults:EditorValues={name:"",protocol:"http",content:"",source_url:"",max_accounts:3,refresh_interval:600,status:true,owner:0}
function ProxyEditor({source=false,admin=false,row,onSaved}:{source?:boolean;admin?:boolean;row?:Entity;onSaved:()=>void}) {
  const [open,setOpen]=useState(false)
  const [values,setValues]=useState(defaults)
  const boot=useQuery({queryKey:["bootstrap"],queryFn:()=>api<BootstrapData>("bootstrap"),enabled:open})
  useEffect(()=>{if(open)setValues({...defaults,name:String(row?.name??""),protocol:String(row?.protocol??"http"),content:String(row?.content??""),source_url:String(row?.source_url??""),max_accounts:Number(row?.max_accounts??3),refresh_interval:Number(row?.refresh_interval??600),status:Boolean(row?.status??true),owner:Number(row?.owner??0)})},[open,row])
  const mutation=useMutation({mutationFn:()=>post(`${source?"proxy-sources":"proxies"}${row?.id?`/${row.id}`:""}`,{...values,...(admin?{scope:"admin"}:{})}),onSuccess:r=>{toast.success(r.message);setOpen(false);onSaved()},onError:(e:Error)=>toast.error(e.message)})
  return <><Button size="sm" variant={row?"ghost":"default"} onClick={()=>setOpen(true)}>{row?<Pencil/>:<Plus/>}{row?"编辑":source?"添加提取源":"添加静态 IP"}</Button><Dialog open={open} onOpenChange={setOpen}><DialogContent className="sm:max-w-lg"><DialogHeader><DialogTitle>{row?"编辑":"添加"}{source?"自动提取源":"静态 IP"}</DialogTitle><DialogDescription>保存后将按当前所属用户进行隔离。</DialogDescription></DialogHeader><div className="grid gap-4 sm:grid-cols-2"><Field label="名称" value={values.name} onChange={name=>setValues(v=>({...v,name}))}/><div className="space-y-2"><Label>协议</Label><select className="h-8 w-full rounded-lg border bg-background px-2.5 text-sm" value={values.protocol} onChange={e=>setValues(v=>({...v,protocol:e.target.value}))}><option value="http">HTTP</option><option value="socks5">SOCKS5</option></select></div>{source?<><div className="space-y-2 sm:col-span-2"><Field label="提取链接" value={values.source_url} onChange={source_url=>setValues(v=>({...v,source_url}))}/></div><Field label="同步周期（秒）" type="number" value={String(values.refresh_interval)} onChange={refresh_interval=>setValues(v=>({...v,refresh_interval:Number(refresh_interval)}))}/></>:<><div className="space-y-2 sm:col-span-2"><Field label="连接地址" value={values.content} onChange={content=>setValues(v=>({...v,content}))}/><p className="text-xs leading-5 text-muted-foreground">支持 hostname:port:username:password、username:password:hostname:port、username:password@hostname:port、hostname:port@username:password</p></div><Field label="账号容量" type="number" value={String(values.max_accounts)} onChange={max_accounts=>setValues(v=>({...v,max_accounts:Number(max_accounts)}))}/></>}<div className="space-y-2"><Label>当前用户</Label><Input value={String(boot.data?.data.user?.username??"加载中…")} readOnly/></div><div className="flex items-center justify-between rounded-lg border p-3 sm:col-span-2"><Label>启用</Label><Switch checked={values.status} onCheckedChange={status=>setValues(v=>({...v,status}))}/></div></div><DialogFooter><Button variant="outline" onClick={()=>setOpen(false)}>取消</Button><Button disabled={mutation.isPending} onClick={()=>mutation.mutate()}>保存</Button></DialogFooter></DialogContent></Dialog></>
}
function Field({label,value,onChange,type="text"}:{label:string;value:string;onChange:(v:string)=>void;type?:string}){return <div className="space-y-2"><Label>{label}</Label><Input type={type} value={value} onChange={e=>onChange(e.target.value)}/></div>}
function DeleteButton({title,onConfirm}:{title:string;onConfirm:()=>void}) {
  return <AlertDialog><AlertDialogTrigger render={<Button size="sm" variant="ghost"/> }><Trash2/><span className="sr-only">删除</span></AlertDialogTrigger><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>{title}</AlertDialogTitle><AlertDialogDescription>此操作会立即生效，关联分配可能同时解除。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>取消</AlertDialogCancel><AlertDialogAction variant="destructive" onClick={onConfirm}>确认删除</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
}
