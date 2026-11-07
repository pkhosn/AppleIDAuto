import "@testing-library/jest-dom/vitest"
import {cleanup,fireEvent,render,screen,waitFor} from "@testing-library/react"
import {QueryClient,QueryClientProvider} from "@tanstack/react-query"
import {afterEach,it,expect,vi} from "vitest"
import {ShareSettings} from "./share-settings"
afterEach(()=>{cleanup();vi.restoreAllMocks()})
it("loads inherited settings, previews custom text and closes only after save",async()=>{
 let saved:unknown
 vi.spyOn(globalThis,"fetch").mockImplementation(async(_input,init)=>{
  if(init?.method==="POST")saved=JSON.parse(String(init.body))
  return new Response(JSON.stringify({success:true,message:"ok",data:{mode:"inherit",text:"",default_enabled:true,default_text:"站点水印"}}),{headers:{"Content-Type":"application/json"}})
 })
 render(<QueryClientProvider client={new QueryClient()}><ShareSettings/></QueryClientProvider>)
 fireEvent.click(screen.getByRole("button",{name:"分享设置"}))
 await screen.findByLabelText("水印模式")
 expect(screen.getByText("站点水印")).toBeVisible()
 fireEvent.change(screen.getByLabelText("水印模式"),{target:{value:"custom"}})
 expect(screen.getByRole("button",{name:"保存设置"})).toBeDisabled()
 fireEvent.change(screen.getByLabelText("水印文字"),{target:{value:"我的分享"}})
 expect(screen.getByText("我的分享")).toBeVisible()
 fireEvent.change(screen.getByLabelText("关闭分享提示文字"),{target:{value:"维护中"}})
 fireEvent.click(screen.getByRole("button",{name:"保存设置"}))
 await waitFor(()=>expect(saved).toEqual({mode:"custom",text:"我的分享",closed_text:"维护中"}))
 await waitFor(()=>expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
})
