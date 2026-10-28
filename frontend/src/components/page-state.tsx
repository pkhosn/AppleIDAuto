import { AlertCircle, Inbox } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"

export function PageSkeleton() {
  return <div className="grid gap-4"><Skeleton className="h-24 w-full" /><Skeleton className="h-80 w-full" /></div>
}

export function EmptyState({ title = "暂无数据", description = "当前条件下没有可显示的内容。" }) {
  return <div className="flex min-h-48 flex-col items-center justify-center gap-2 text-center"><Inbox className="size-9 text-muted-foreground/60" /><p className="font-medium">{title}</p><p className="text-sm text-muted-foreground">{description}</p></div>
}

export function ErrorState({ error, retry }: { error: Error; retry?: () => void }) {
  return <Card><CardContent className="flex min-h-48 flex-col items-center justify-center gap-3 text-center"><AlertCircle className="size-9 text-destructive" /><div><p className="font-medium">加载失败</p><p className="mt-1 text-sm text-muted-foreground">{error.message}</p></div>{retry && <Button variant="outline" onClick={retry}>重新加载</Button>}</CardContent></Card>
}
