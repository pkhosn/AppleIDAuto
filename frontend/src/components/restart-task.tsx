import { useState } from "react"
import { RotateCcw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from "@/components/ui/alert-dialog"

export function RestartTask({ onConfirm, disabled, pending = false }: { onConfirm: () => void; disabled: boolean; pending?: boolean }) {
  const [open, setOpen] = useState(false)
  const confirm = () => {
    setOpen(false)
    onConfirm()
  }
  return <AlertDialog open={open} onOpenChange={setOpen}><AlertDialogTrigger render={<Button size="sm" variant="outline" disabled={disabled} />}><RotateCcw className={pending ? "animate-spin" : undefined} />{pending ? "提交中…" : "运行到期项"}</AlertDialogTrigger>
    <AlertDialogContent><AlertDialogHeader><AlertDialogTitle>立即运行全部到期功能？</AlertDialogTitle>
      <AlertDialogDescription>仅运行已到期且已启用的功能，不会提前触发尚未到期的设备移除或改密。待确认的破坏性操作只会进入只读核验。</AlertDialogDescription></AlertDialogHeader>
      <AlertDialogFooter><AlertDialogCancel>取消</AlertDialogCancel><AlertDialogAction onClick={confirm}>确认运行到期项</AlertDialogAction></AlertDialogFooter>
    </AlertDialogContent></AlertDialog>
}
