import { Component, useCallback } from "react"
import type { ErrorInfo, ReactNode } from "react"
import { useQuery } from "@tanstack/react-query"
import { BrowserRouter, HashRouter, Navigate, Route, Routes } from "react-router-dom"
import { api, setCsrfToken } from "@/lib/api"
import type { BootstrapData } from "@/types"
import { AppShell } from "@/components/app-shell"
import { ErrorState, PageSkeleton } from "@/components/page-state"
import { LoginPage } from "@/pages/login-page"
import { DashboardPage } from "@/pages/dashboard-page"
import { ListPage } from "@/pages/list-page"
import { ProxyPage } from "@/pages/proxy-page"
import { PlansPage } from "@/pages/plans-page"
import { ProfilePage } from "@/pages/profile-page"
import { NotificationsPage } from "@/pages/notifications-page"
import { SettingsPage } from "@/pages/settings-page"
import { MailRecoveryPage } from "@/pages/mail-recovery-page"
import { TemplatesPage } from "@/pages/templates-page"
import { RedeemPage } from "@/pages/redeem-page"
import { NotFoundPage } from "@/pages/not-found-page"

function Application() {
  const bootstrap = useQuery({ queryKey: ["bootstrap"], queryFn: async () => { const response = await api<BootstrapData>("bootstrap"); setCsrfToken(response.data.csrf_token); return response }, staleTime: 30_000, retry: 1 })
  const refresh = useCallback(async()=>{await bootstrap.refetch()},[bootstrap])
  if (bootstrap.isLoading) return <div className="p-6"><PageSkeleton /></div>
  if (bootstrap.error) return <div className="p-6"><ErrorState error={bootstrap.error} retry={() => bootstrap.refetch()} /></div>
  const session = bootstrap.data!.data
  if (!session.authenticated) return <LoginPage bootstrap={session} onAuthenticated={refresh} />
  const preview=window.location.pathname.startsWith("/react-preview")
  const user=preview?{home:"",account:"accounts",task:"tasks",record:"records",share:"shares",template:"templates",proxy:"proxies",plan:"plans",order:"orders",redeem:"redeem",notify:"notifications",info:"profile"}:{home:"user/index",account:"user/account",task:"user/task",record:"user/record",share:"user/share",template:"user/template",proxy:"user/proxy",plan:"user/plan",order:"user/order",redeem:"user/redeem",notify:"user/notify",info:"user/info"}
  const adminRoot=preview?"admin":session.admin_base.replace(/^\//,"")
  const admin={home:adminRoot,user:`${adminRoot}/${preview?"users":"user"}`,account:`${adminRoot}/${preview?"accounts":"account"}`,task:`${adminRoot}/${preview?"tasks":"task"}`,record:`${adminRoot}/${preview?"records":"record"}`,share:`${adminRoot}/${preview?"shares":"share"}`,template:`${adminRoot}/template`,proxy:`${adminRoot}/${preview?"proxies":"proxy"}`,mail:`${adminRoot}/mail-recovery`,plan:`${adminRoot}/${preview?"plans":"plan"}`,order:`${adminRoot}/${preview?"orders":"order"}`,redeem:`${adminRoot}/${preview?"redeem-codes":"redeem"}`,payment:`${adminRoot}/payment`,site:`${adminRoot}/site`,security:`${adminRoot}/security`}
  const guard=(child:ReactNode)=><AdminOnly allowed={Boolean(session.user?.is_admin)}>{child}</AdminOnly>
  return <Routes><Route element={<AppShell bootstrap={session} />}>
    <Route index element={<DashboardPage />} />
    {user.home&&<Route path={user.home} element={<DashboardPage/>}/>}<Route path={user.account} element={<ListPage kind="accounts"/>}/><Route path={`${user.account}/:id`} element={<ListPage kind="accounts"/>}/><Route path={user.task} element={<ListPage kind="tasks"/>}/><Route path={user.record} element={<ListPage kind="records"/>}/><Route path={user.share} element={<ListPage kind="shares"/>}/><Route path={user.template} element={<TemplatesPage/>}/><Route path={user.proxy} element={<ProxyPage/>}/><Route path={user.plan} element={<PlansPage/>}/><Route path={user.order} element={<ListPage kind="orders"/>}/><Route path={user.redeem} element={<RedeemPage/>}/><Route path={user.notify} element={<NotificationsPage/>}/><Route path={user.info} element={<ProfilePage/>}/>
    <Route path={admin.home} element={guard(<DashboardPage admin/>)}/><Route path={`${admin.home}/index`} element={guard(<DashboardPage admin/>)}/><Route path={admin.user} element={guard(<ListPage kind="users" admin/>)}/><Route path={admin.account} element={guard(<ListPage kind="accounts" admin/>)}/><Route path={admin.task} element={guard(<ListPage kind="tasks" admin/>)}/><Route path={admin.record} element={guard(<ListPage kind="records" admin/>)}/><Route path={admin.share} element={guard(<ListPage kind="shares" admin/>)}/><Route path={admin.template} element={guard(<TemplatesPage/>)}/><Route path={admin.proxy} element={guard(<ProxyPage admin/>)}/><Route path={admin.mail} element={guard(<MailRecoveryPage/>)}/><Route path={admin.plan} element={guard(<PlansPage admin/>)}/><Route path={admin.order} element={guard(<ListPage kind="orders" admin/>)}/><Route path={admin.redeem} element={guard(<ListPage kind="redeem-codes" admin/>)}/><Route path={admin.site} element={guard(<SettingsPage section="branding"/>)}/><Route path={admin.security} element={guard(<SettingsPage section="registration"/>)}/><Route path={admin.payment} element={guard(<SettingsPage section="payment"/>)}/>
    <Route path="*" element={<NotFoundPage />} />
  </Route></Routes>
}

function AdminOnly({allowed,children}:{allowed:boolean;children:ReactNode}){return allowed?children:<Navigate to="/" replace/>}

export class AppErrorBoundary extends Component<{children:ReactNode},{error:Error|null}>{state:{error:Error|null}={error:null};static getDerivedStateFromError(error:Error){return{error}}componentDidCatch(error:Error,info:ErrorInfo){console.error("React control panel crashed",error,info)}render(){return this.state.error?<div className="p-6"><ErrorState error={this.state.error} retry={()=>window.location.reload()}/></div>:this.props.children}}

export default function App(){const Router=window.location.pathname.startsWith("/react-preview")?HashRouter:BrowserRouter;return <AppErrorBoundary><Router><Application/></Router></AppErrorBoundary>}
