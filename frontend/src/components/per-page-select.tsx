import { useCallback, useEffect, useState } from "react"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"

/** Page sizes offered by every paginated list, shared with the backend clamp (1-100). */
export const PER_PAGE_OPTIONS = [10, 25, 50, 100] as const
export const DEFAULT_PER_PAGE = 25

const storageKey = (scopeKey: string) => `panel:per-page:${scopeKey}`

function normalize(value: unknown, fallback: number) {
  const parsed = Number(value)
  return (PER_PAGE_OPTIONS as readonly number[]).includes(parsed) ? parsed : fallback
}

/**
 * Remembers the page size of one list across reloads. The scope key keeps each
 * list (shares, accounts, proxies, ...) independent from the others.
 */
export function usePerPage(scopeKey: string, fallback: number = DEFAULT_PER_PAGE) {
  const [perPage, setPerPage] = useState(() => {
    try {
      return normalize(window.localStorage.getItem(storageKey(scopeKey)), fallback)
    } catch {
      return fallback
    }
  })
  useEffect(() => {
    try {
      window.localStorage.setItem(storageKey(scopeKey), String(perPage))
    } catch {
      // Private mode or disabled storage: fall back to per-session state only.
    }
  }, [scopeKey, perPage])
  const update = useCallback((next: number) => setPerPage(normalize(next, fallback)), [fallback])
  return [perPage, update] as const
}

export function PerPageSelect({ value, onChange, disabled = false, label = "每页行数" }: {
  value: number
  onChange: (value: number) => void
  disabled?: boolean
  label?: string
}) {
  return <div className="flex items-center gap-2 text-sm text-muted-foreground">
    <span>{label}</span>
    <Select value={String(value)} onValueChange={next => onChange(Number(next))} disabled={disabled}>
      <SelectTrigger size="sm" aria-label={label}><SelectValue>{`${value} 条`}</SelectValue></SelectTrigger>
      <SelectContent>{PER_PAGE_OPTIONS.map(option => <SelectItem key={option} value={String(option)}>{`${option} 条`}</SelectItem>)}</SelectContent>
    </Select>
  </div>
}
