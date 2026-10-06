import {defineConfig} from "@playwright/test";
export default defineConfig({testDir:"./tests/browser",testMatch:"gui01-readonly.spec.ts",workers:1,outputDir:".gui01-readonly-results",
  reporter:[["list"],["json",{outputFile:"../docs/tracker/evidence/gui01-overview-sol/browser-readonly-results.json"}]],
  use:{baseURL:"http://127.0.0.1:4177",headless:true,viewport:{width:1440,height:1080},launchOptions:{executablePath:"/home/sarmad/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome"}},
  webServer:{command:"npx vite build && cd .. && GUI01_TEST_MODE=READ_ONLY_GUI ./venv/bin/python -m tests.owner_frontend_server 4177",url:"http://127.0.0.1:4177/",timeout:120000}});
