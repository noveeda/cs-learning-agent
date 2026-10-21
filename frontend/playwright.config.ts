// 파일 역할: 브라우저 테스트용 서버 포트·화면 크기·선택적 실행 브라우저 경로 설정
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
