import { defineConfig, devices } from "@playwright/test"

const configuredBaseURL = process.env.PLAYWRIGHT_BASE_URL?.trim()
if (!configuredBaseURL) {
  throw new Error("请显式设置 PLAYWRIGHT_BASE_URL，指向自己控制的隔离测试站点")
}
const baseURL = new URL(configuredBaseURL)
if (!["http:", "https:"].includes(baseURL.protocol) || baseURL.search || baseURL.hash) {
  throw new Error("PLAYWRIGHT_BASE_URL 必须为不含查询参数或片段的 HTTP(S) 绝对地址")
}
if (!baseURL.pathname.endsWith("/")) baseURL.pathname += "/"

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  fullyParallel: true,
  retries: 1,
  reporter: "list",
  use: {
    baseURL: baseURL.href,
    ignoreHTTPSErrors: true,
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
})
