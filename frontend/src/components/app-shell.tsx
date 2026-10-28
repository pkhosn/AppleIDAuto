import {
  Activity, BellRing, Boxes, CheckCircle2, ChevronDown, CircleUserRound, CreditCard, FileClock,
  Gauge, KeyRound, Layers3, LayoutTemplate, LogOut, Moon, Network, Package, PanelTop, Settings2, Share2,
  ShieldCheck, Sun, UsersRound, Workflow,
} from "lucide-react"
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom"
import { useTheme } from "next-themes"
import { toast } from "sonner"
import { useQueryClient } from "@tanstack/react-query"
import { post } from "@/lib/api"
import type { BootstrapData } from "@/types"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent,
  SidebarGroupLabel, SidebarHeader, SidebarInset, SidebarMenu, SidebarMenuButton,
  SidebarMenuItem, SidebarProvider, SidebarRail, SidebarTrigger,
} from "@/components/ui/sidebar"

const userItems = [["工作台","index",Gauge],["Apple账号","account",Boxes],["自动化任务","task",Workflow],["执行记录","record",FileClock],["分享页","share",Share2],["模板管理","template",LayoutTemplate],["代理网络","proxy",Network],["订阅","plan",Package],["我的订单","order",CreditCard],["兑换码","redeem",KeyRound],["消息推送","notify",BellRing],["个人设置","info",CircleUserRound]] as const
const adminItems = [["工作台","index",Gauge],["用户与权限","user",UsersRound],["Apple账号","account",Boxes],["自动化任务","task",Workflow],["执行记录","record",Activity],["分享页","share",Share2],["模板管理","template",LayoutTemplate],["代理网络","proxy",Network],["邮件恢复","mail-recovery",CheckCircle2],["套餐配置","plan",Layers3],["订单中心","order",CreditCard],["兑换码","redeem",KeyRound],["支付配置","payment",CreditCard],["品牌与站点","site",PanelTop],["访问安全","security",ShieldCheck]] as const

export function AppShell({ bootstrap }: { bootstrap: BootstrapData }) {
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { resolvedTheme, setTheme } = useTheme()
  const preview = window.location.pathname.startsWith("/react-preview")
  const adminBase = preview ? "/admin" : (bootstrap.admin_base || "/admin")
  const admin = location.pathname === adminBase || location.pathname.startsWith(`${adminBase}/`)
  const canAdmin = Boolean(bootstrap.user?.is_admin)
  const previewUser:Record<string,string>={index:"/",account:"/accounts",task:"/tasks",record:"/records",share:"/shares",template:"/templates",proxy:"/proxies",plan:"/plans",order:"/orders",redeem:"/redeem",notify:"/notifications",info:"/profile"}
  const previewAdmin:Record<string,string>={index:"/admin",user:"/admin/users",account:"/admin/accounts",task:"/admin/tasks",record:"/admin/records",share:"/admin/shares",template:"/admin/template",proxy:"/admin/proxies","mail-recovery":"/admin/mail-recovery",plan:"/admin/plans",order:"/admin/orders",redeem:"/admin/redeem-codes",payment:"/admin/payment",site:"/admin/site",security:"/admin/security"}
  const navigation = (admin ? adminItems : userItems).map(([label,key,Icon])=>[label,preview?(admin?previewAdmin[key]:previewUser[key]):admin?`${adminBase}/${key}`:`/user/${key}`,Icon] as const)
  const siteName = bootstrap.branding?.site_name || "AppleID 自动化管理"

  async function logout() {
    try {
      await post("auth/logout")
      queryClient.clear()
      if (window.location.pathname.startsWith("/react-preview")) { window.location.hash = "#/login"; window.location.reload() }
      else window.location.assign("/")
    } catch (error) { toast.error(error instanceof Error ? error.message : "退出失败") }
  }

  return (
    <SidebarProvider>
      <Sidebar collapsible="icon">
        <SidebarHeader className="h-20 justify-center border-b px-4">
          <NavLink to={preview?"/":"/user/index"} className="flex items-center gap-2 overflow-hidden px-1">
            <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground"><Settings2 className="size-5" /></span>
            <span className="truncate text-base font-semibold group-data-[collapsible=icon]:hidden">{siteName}</span>
          </NavLink>
        </SidebarHeader>
        <SidebarContent>
          <SidebarGroup className="p-3">
            <SidebarGroupLabel className="h-10 px-3 text-sm">{admin ? "管理控制台" : "业务中心"}</SidebarGroupLabel>
            <SidebarGroupContent><SidebarMenu>{navigation.map(([label, path, Icon]) => (
              <SidebarMenuItem key={path}><SidebarMenuButton tooltip={label} isActive={location.pathname === path} render={<NavLink to={path} />}><Icon /><span>{label}</span></SidebarMenuButton></SidebarMenuItem>
            ))}</SidebarMenu></SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>
        <SidebarFooter className="border-t p-3">
          <SidebarMenu><SidebarMenuItem>
            <DropdownMenu><DropdownMenuTrigger render={<SidebarMenuButton size="lg" />}>
              <Avatar className="size-10"><AvatarFallback className="bg-primary/10 text-base text-primary">{bootstrap.user?.username?.slice(0, 1).toUpperCase()}</AvatarFallback></Avatar>
              <div className="min-w-0 flex-1 text-left text-base leading-tight"><span className="block truncate font-medium">{bootstrap.user?.username}</span><span className="block truncate text-sm text-muted-foreground">{canAdmin ? "管理员" : "标准用户"}</span></div><ChevronDown className="ml-auto size-5" />
            </DropdownMenuTrigger><DropdownMenuContent side="top" align="end" className="w-56">
              <DropdownMenuGroup>
                <DropdownMenuLabel>当前工作区</DropdownMenuLabel>
                {canAdmin && <DropdownMenuItem onClick={() => navigate(admin ? (preview?"/":"/user/index") : (preview?"/admin":`${adminBase}/index`))}><UsersRound />{admin ? "切换至用户端" : "切换至管理端"}</DropdownMenuItem>}
                <DropdownMenuItem onClick={() => navigate(preview?"/profile":"/user/info")}><CircleUserRound />个人设置</DropdownMenuItem>
              </DropdownMenuGroup>
              <DropdownMenuSeparator /><DropdownMenuItem variant="destructive" onClick={logout}><LogOut />安全退出</DropdownMenuItem>
            </DropdownMenuContent></DropdownMenu>
          </SidebarMenuItem></SidebarMenu>
        </SidebarFooter><SidebarRail />
      </Sidebar>
      <SidebarInset>
        <header className="sticky top-0 z-20 flex h-20 items-center gap-4 border-b bg-background/90 px-5 backdrop-blur md:px-8">
          <SidebarTrigger className="size-11 [&_svg]:size-5" />
          <div className="min-w-0 flex-1"><h1 className="truncate text-lg font-semibold">{navigation.find(([,path])=>path===location.pathname)?.[0] ?? (admin ? "管理控制台" : "工作台")}</h1><p className="hidden text-sm text-muted-foreground sm:block">安全、清晰地管理账号与自动化服务</p></div>
          <Button variant="ghost" size="icon" className="size-11 [&_svg]:size-5" aria-label="切换主题" onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}>{resolvedTheme === "dark" ? <Sun /> : <Moon />}</Button>
        </header>
        <main className="mx-auto w-full max-w-[1600px] flex-1 p-5 md:p-8"><Outlet /></main>
      </SidebarInset>
    </SidebarProvider>
  )
}
