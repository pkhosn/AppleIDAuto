import { Search } from "lucide-react"
import { useMemo, useState } from "react"
import type { Entity } from "@/types"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { EmptyState } from "@/components/page-state"
import { cn } from "@/lib/utils"

export type Column = { key: string; label: string; className?: string; mobile?: boolean; render?: (value: unknown, row: Entity) => React.ReactNode }

function primitive(value: unknown) {
  if (value === null || value === undefined || value === "") return "—"
  if (typeof value === "boolean") return value ? "是" : "否"
  if (Array.isArray(value)) return value.join("、") || "—"
  if (typeof value === "object") return JSON.stringify(value)
  return String(value)
}

export function statusNode(value: unknown) {
  const normalized = String(value).toLowerCase()
  const labels: Record<string, string> = { "1": "已启用", "0": "已停用", "true": "已启用", "false": "已停用", enabled: "已启用", disabled: "已停用", active: "有效", success: "成功", completed: "已完成", healthy: "健康", paid: "已支付", running: "执行中", normal: "正常", failed: "失败", error: "异常", expired: "已过期", cancelled: "已取消", unhealthy: "异常", pending: "等待中", unused: "未使用", used: "已使用" }
  const text = labels[normalized] ?? primitive(value)
  const good = ["1", "true", "enabled", "active", "success", "completed", "healthy", "paid", "running", "normal"].includes(normalized)
  const bad = ["0", "false", "disabled", "failed", "error", "expired", "cancelled", "unhealthy"].includes(normalized)
  return <Badge variant={bad ? "destructive" : good ? "default" : "secondary"} className={good ? "bg-teal-600 text-white dark:bg-teal-500" : ""}>{text}</Badge>
}

export function EntityTable({ rows, columns, searchPlaceholder = "搜索当前列表", searchValue, onSearchChange, actions, hideSearch = false }: { rows: Entity[]; columns: Column[]; searchPlaceholder?: string; searchValue?: string; onSearchChange?: (value: string) => void; actions?: (row: Entity) => React.ReactNode; hideSearch?: boolean }) {
  const [localQuery, setLocalQuery] = useState("")
  const query = onSearchChange ? (searchValue ?? "") : localQuery
  const visible = useMemo(() => onSearchChange ? rows : rows.filter(row => !query || JSON.stringify(row).toLowerCase().includes(query.toLowerCase())), [rows, query, onSearchChange])
  const mobileColumns = useMemo(() => {
    const selected = columns.filter(column => column.mobile)
    return selected.length ? selected : columns.slice(0, 6)
  }, [columns])
  return <div className="space-y-3">
    {!hideSearch && <div className="relative max-w-sm"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input value={query} onChange={e => onSearchChange ? onSearchChange(e.target.value) : setLocalQuery(e.target.value)} className="pl-9" placeholder={searchPlaceholder} aria-label={searchPlaceholder} /></div>}
    <Card className="overflow-hidden py-0"><div className="hidden overflow-x-auto md:block"><Table><TableHeader><TableRow>{columns.map(c => <TableHead key={c.key} className="h-10 whitespace-nowrap text-xs">{c.label}</TableHead>)}{actions && <TableHead className="h-10 text-right text-xs">操作</TableHead>}</TableRow></TableHeader><TableBody>
      {visible.map((row, index) => <TableRow key={String(row.id ?? index)}>{columns.map(c => <TableCell key={c.key} className={cn("max-w-64 truncate py-2.5 text-sm", c.className)}>{c.render ? c.render(row[c.key], row) : primitive(row[c.key])}</TableCell>)}{actions && <TableCell className="py-2 text-right">{actions(row)}</TableCell>}</TableRow>)}
    </TableBody></Table></div>
      <CardContent className="grid gap-3 p-3 md:hidden">{visible.map((row, index) => <div key={String(row.id ?? index)} className="rounded-lg border p-3"><div className="grid gap-2">{mobileColumns.map(c => <div key={c.key} className="flex items-start justify-between gap-3 text-sm"><span className="shrink-0 text-muted-foreground">{c.label}</span><span className="min-w-0 break-all text-right">{c.render ? c.render(row[c.key], row) : primitive(row[c.key])}</span></div>)}</div>{actions && <div className="mt-3 flex justify-end border-t pt-3">{actions(row)}</div>}</div>)}</CardContent>
      {!visible.length && <EmptyState title={query ? "没有匹配结果" : "暂无数据"} />}
    </Card>
  </div>
}
