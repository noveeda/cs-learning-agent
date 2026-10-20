import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/browser",
  use: {
    baseURL: "http://127.0.0.1:5197",
    viewport: { width: 1440, height: 900 },
    launchOptions: process.env.BROWSER_EXECUTABLE_PATH
      ? { executablePath: process.env.BROWSER_EXECUTABLE_PATH }
      : {},
  },
  webServer: {
    command: "npm run dev -- --port 5197",
    url: "http://127.0.0.1:5197",
  },
});
