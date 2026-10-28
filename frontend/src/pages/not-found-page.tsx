import { Link } from "react-router-dom"
import { ArrowLeft } from "lucide-react"
import { Button } from "@/components/ui/button"

export function NotFoundPage(){return <div className="grid min-h-[65vh] place-items-center text-center"><div><p className="text-sm font-medium text-primary">404</p><h2 className="mt-2 text-3xl font-semibold">页面不存在</h2><p className="mt-2 text-sm text-muted-foreground">该页面可能已移动，或您没有访问权限。</p><Button className="mt-6" render={<Link to="/"/>}><ArrowLeft/>返回工作台</Button></div></div>}
