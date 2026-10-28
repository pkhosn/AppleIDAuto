export type Entity = Record<string, unknown> & { id?: number | string }

export type SessionUser = Entity & {
  id: number
  username: string
  is_admin?: number | boolean
}

export type Branding = {
  site_name?: string
  site_subtitle?: string
  logo_url?: string
  admin_base?: string
}

export type BootstrapData = {
  authenticated: boolean
  user: SessionUser | null
  csrf_token: string
  admin_base: string
  branding: Branding
  registration: Record<string, unknown>
}
