import {useEffect,useState} from "react"
import {useMutation,useQuery} from "@tanstack/react-query"
import {toast} from "sonner"
import {api,post} from "@/lib/api"
import {Button} from "@/components/ui/button"
import {Input} from "@/components/ui/input"
import {Label} from "@/components/ui/label"
import {Dialog,DialogContent,DialogHeader,DialogTitle,DialogDescription,DialogFooter} from "@/components/ui/dialog"

type Settings={mode:"inherit"|"custom"|"off";text:string;default_enabled:boolean;default_text:string}
export function ShareSettings(){
  const [open,setOpen]=useState(false)
  const [mode,setMode]=useState<Settings["mode"]>("inherit")
  const [text,setText]=useState("")
  const query=useQuery({queryKey:["share-settings"],queryFn:()=>api<Settings>("share-settings"),enabled:open})
  useEffect(()=>{if(open&&query.data){setMode(query.data.data.mode);setText(query.data.data.text)}},[open,query.data])
  const save=useMutation({mutationFn:()=>post("share-settings",{mode,text}),onSuccess:r=>{toast.success(r.message);query.refetch();setOpen(false)},onError:(e:Error)=>toast.error(e.message)})
  const visible=mode==="custom"||(mode==="inherit"&&query.data?.data.default_enabled)
  return <><Button size="sm" variant="outline" onClick={()=>setOpen(true)}>分享设置</Button><Dialog open={open} onOpenChange={setOpen}><DialogContent><DialogHeader><DialogTitle>分享设置</DialogTitle><DialogDescription>此设置对你名下所有分享页及 iframe 生效。</DialogDescription></DialogHeader>{query.isPending?<p>加载中…</p>:query.isError?<p role="alert">加载失败，请关闭后重试。</p>:<div className="space-y-4"><div className="space-y-2"><Label htmlFor="watermark-mode">水印模式</Label><select id="watermark-mode" className="h-9 w-full rounded-lg border bg-background px-3 text-sm" value={mode} onChange={e=>setMode(e.target.value as Settings["mode"])}><option value="inherit">跟随站点默认</option><option value="custom">自定义</option><option value="off">关闭水印</option></select></div>{mode==="custom"&&<div className="space-y-2"><Label htmlFor="watermark-text">水印文字</Label><Input id="watermark-text" maxLength={40} value={text} onChange={e=>setText(e.target.value)} placeholder="请输入 1 至 40 个字符"/></div>}<div className="relative grid min-h-36 place-items-center overflow-hidden rounded-lg border bg-muted/30"><span className="text-sm text-muted-foreground">水印效果预览</span>{visible&&<span className="pointer-events-none absolute max-w-full -rotate-12 break-words text-center text-lg font-semibold text-red-500/45">{mode==="custom"?text:query.data?.data.default_text}</span>}</div></div>}<DialogFooter><Button variant="outline" onClick={()=>setOpen(false)}>取消</Button><Button disabled={!query.data||query.isError||save.isPending||(mode==="custom"&&!text.trim())} onClick={()=>save.mutate()}>保存设置</Button></DialogFooter></DialogContent></Dialog></>
}
