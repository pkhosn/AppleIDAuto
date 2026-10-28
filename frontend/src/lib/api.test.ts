import { afterEach, describe, expect, it, vi } from "vitest"
import { api, post, setCsrfToken, withScope } from "@/lib/api"

afterEach(()=>vi.restoreAllMocks())

describe("frontend API client",()=>{
  it("uses the isolated API namespace and same-origin credentials",async()=>{
    const fetchMock=vi.spyOn(globalThis,"fetch").mockResolvedValue(new Response(JSON.stringify({success:true,data:{ok:true},message:"ok",errors:[],meta:{}}),{status:200,headers:{"Content-Type":"application/json"}}))
    const result=await api<{ok:boolean}>("dashboard")
    expect(result.data.ok).toBe(true)
    expect(fetchMock).toHaveBeenCalledWith("/frontend-api/v1/dashboard",expect.objectContaining({credentials:"include"}))
  })

  it("sends the CSRF token on mutations",async()=>{
    setCsrfToken("csrf-test")
    const fetchMock=vi.spyOn(globalThis,"fetch").mockResolvedValue(new Response(JSON.stringify({success:true,data:null,message:"ok",errors:[],meta:{}}),{status:200,headers:{"Content-Type":"application/json"}}))
    await post("profile",{username:"operator"})
    const options=fetchMock.mock.calls[0][1] as RequestInit
    expect(new Headers(options.headers).get("X-CSRF-Token")).toBe("csrf-test")
  })

  it("maps validation responses to ApiError",async()=>{
    vi.spyOn(globalThis,"fetch").mockResolvedValue(new Response(JSON.stringify({success:false,message:"字段错误",errors:{username:"必填"}}),{status:422,headers:{"Content-Type":"application/json"}}))
    await expect(post("profile",{})).rejects.toMatchObject({status:422,message:"字段错误"})
  })

  it("adds admin scope without breaking query strings",()=>{
    expect(withScope("accounts",true)).toBe("accounts?scope=admin")
    expect(withScope("tasks?status=failed",true)).toBe("tasks?status=failed&scope=admin")
  })
})
