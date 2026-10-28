import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { FileCode2, Pencil, Plus, Star, Trash2 } from "lucide-react"
import { toast } from "sonner"
import { api, post } from "@/lib/api"
import type { BootstrapData, Entity } from "@/types"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from "@/components/ui/alert-dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { ErrorState, PageSkeleton } from "@/components/page-state"

export function TemplatesPage() {
  const client = useQueryClient()
  const boot = useQuery({ queryKey: ["bootstrap"], queryFn: () => api<BootstrapData>("bootstrap") })
  const query = useQuery({ queryKey: ["templates"], queryFn: () => api<Entity[]>("templates") })
  const [editor, setEditor] = useState<{ open: boolean; row: Entity | null }>({ open: false, row: null })
  const refresh = () => client.invalidateQueries({ queryKey: ["templates"] })
  const setDefault = useMutation({ mutationFn: (id: string | number) => post(`templates/${id}/default`), onSuccess: r => { toast.success(r.message); refresh() }, onError: (e: Error) => toast.error(e.message) })
  const del = useMutation({ mutationFn: (id: string | number) => api(`templates/${id}`, { method: "DELETE" }), onSuccess: r => { toast.success(r.message); refresh() }, onError: (e: Error) => toast.error(e.message) })
  if (query.isLoading) return <PageSkeleton />
  if (query.error) return <ErrorState error={query.error} retry={() => query.refetch()} />
  const rows = query.data?.data ?? []
  const uid = Number(boot.data?.data?.user?.id ?? 0)
  const canAdmin = Boolean(boot.data?.data?.user?.is_admin)

  const ownerLabel = (owner: unknown, ownerName?: unknown) => {
    const o = Number(owner ?? 0)
    if (o === 0) return "系统模板"
    if (!canAdmin && o === uid) return "我的模板"
    return String(ownerName || "已删除用户")
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h2 className="text-2xl font-semibold">分享页模板</h2><p className="mt-1 text-sm text-muted-foreground">维护分享页“使用说明”内容模板；新建分享页默认采用你的默认模板。</p></div>
        <Button size="sm" onClick={() => setEditor({ open: true, row: null })}><Plus />新建模板</Button>
      </div>
      {rows.length === 0 && <Card><CardContent className="py-10 text-center text-sm text-muted-foreground">暂无模板</CardContent></Card>}
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {rows.map(tpl => {
          const owner = Number(tpl.owner ?? 0)
          const mine = owner === 0 ? canAdmin : owner === uid
          const canEdit = canAdmin || owner === uid
          const isDefault = Boolean(tpl.is_default)
          return (
            <Card key={String(tpl.id)}>
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2"><FileCode2 className="size-4 text-muted-foreground" /><CardTitle className="text-base">{String(tpl.name ?? "未命名")}</CardTitle></div>
                  {isDefault ? <Badge className="bg-amber-500 text-white"><Star className="size-3" />默认</Badge> : <Badge variant="secondary">{ownerLabel(tpl.owner,tpl.owner_name)}</Badge>}
                </div>
                {!isDefault && <CardDescription>{ownerLabel(tpl.owner,tpl.owner_name)}</CardDescription>}
              </CardHeader>
              <CardContent>
                <p className="line-clamp-3 rounded-lg bg-muted/60 p-2 font-mono text-xs text-muted-foreground">{String(tpl.html ?? "") || "（空内容）"}</p>
                <div className="mt-3 flex flex-wrap justify-end gap-1">
                  <Button size="sm" variant="ghost" disabled={setDefault.isPending} onClick={() => setDefault.mutate(tpl.id!)}><Star className="size-4" />设为默认</Button>
                  <Button size="sm" variant="ghost" disabled={!canEdit} onClick={() => setEditor({ open: true, row: tpl })}><Pencil className="size-4" />编辑</Button>
                  {canEdit && (
                    <AlertDialog>
                      <AlertDialogTrigger render={<Button size="sm" variant="ghost" disabled={!mine} />}><Trash2 className="size-4" /><span className="sr-only">删除模板</span></AlertDialogTrigger>
                      <AlertDialogContent><AlertDialogHeader><AlertDialogTitle>删除模板？</AlertDialogTitle><AlertDialogDescription>删除后不可恢复，已使用的分享页不受影响。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>取消</AlertDialogCancel><AlertDialogAction variant="destructive" onClick={() => del.mutate(tpl.id!)}>确认删除</AlertDialogAction></AlertDialogFooter></AlertDialogContent>
                    </AlertDialog>
                  )}
                </div>
              </CardContent>
            </Card>
          )
        })}
      </div>
      <TemplateEditor state={editor} onOpenChange={(open) => setEditor(s => ({ ...s, open }))} onSaved={refresh} />
    </div>
  )
}

function TemplateEditor({ state, onOpenChange, onSaved }: { state: { open: boolean; row: Entity | null }; onOpenChange: (open: boolean) => void; onSaved: () => void }) {
  const [name, setName] = useState("")
  const [html, setHtml] = useState("")
  const [noticeHtml, setNoticeHtml] = useState("")
  useEffect(() => {
    if (state.open) { setName(String(state.row?.name ?? "")); setHtml(String(state.row?.html ?? "")); setNoticeHtml(String(state.row?.notice_html ?? "")) }
  }, [state])
  const mutation = useMutation({
    mutationFn: () => post(`templates${state.row?.id ? `/${state.row.id}` : ""}`, { name, html, notice_html: noticeHtml }),
    onSuccess: (r) => { toast.success(r.message); onOpenChange(false); onSaved() },
    onError: (e: Error) => toast.error(e.message),
  })
  return (
    <Dialog open={state.open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader><DialogTitle>{state.row ? "编辑模板" : "新建模板"}</DialogTitle><DialogDescription>模板内容会插入分享页的“使用说明”区域，可用 HTML 自定义样式。</DialogDescription></DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2"><Label>模板名称</Label><Input value={name} onChange={e => setName(e.target.value)} placeholder="例如：默认模板 / 极简说明" /></div>
          <div className="space-y-2"><Label>账号说明（HTML 内容）</Label><Textarea value={html} onChange={e => setHtml(e.target.value)} rows={16} className="min-h-[280px] font-mono text-xs leading-relaxed" placeholder={'<section class="share-welcome"><h2>账号使用说明</h2>…</section>'} /></div>
          <div className="space-y-2">
            <Label>使用说明 / 使用提醒</Label>
            <Textarea value={noticeHtml} onChange={e => setNoticeHtml(e.target.value)} rows={10} className="min-h-[200px] font-mono text-xs leading-relaxed" placeholder={'<section class="share-notice">…</section>\n<section class="share-guidance">…</section>'} />
            <p className="text-xs text-muted-foreground">分享页顶部“使用说明 / 使用提醒”区域；留空则使用系统默认文案。支持 share-notice、share-guidance 及 share-tag/share-refresh 等现有样式类。</p>
          </div>
        </div>
        <DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>取消</Button><Button disabled={mutation.isPending || !name.trim() || !html.trim()} onClick={() => mutation.mutate()}>保存模板</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
