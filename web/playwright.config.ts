import { defineConfig } from "@playwright/test";

const PORT = 8766;
export default defineConfig({
  testDir: "tests/e2e",
  timeout: 60_000,
  expect: { timeout: 15_000, toHaveScreenshot: { maxDiffPixelRatio: 0.002, animations: "disabled" } },
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    viewport: { width: 1440, height: 900 },
    acceptDownloads: true,
    launchOptions: { args: ["--use-gl=swiftshader", "--enable-webgl", "--ignore-gpu-blocklist"] },
  },
  webServer: {
    command: `cd .. && uv run python scripts/e2e_server.py ${PORT}`,
    url: `http://127.0.0.1:${PORT}/api/meta`,
    timeout: 180_000,
    reuseExistingServer: !process.env.CI,
  },
});
