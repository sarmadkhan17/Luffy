import {defineConfig} from "@playwright/test";
import {homedir} from "node:os";
export default defineConfig({testDir:"./tests/browser",testMatch:["stage8.spec.ts","live.spec.ts"],workers:1,timeout:30000,
  reporter:[["list"],["json",{outputFile:"../docs/superpowers/evidence/stage8-owner-os-closure-r1/playwright.json"}]],
  outputDir:".stage8-test-results",
  use:{baseURL:"http://127.0.0.1:4176",headless:true,viewport:{width:1440,height:1080},launchOptions:{executablePath:process.env.PREVIEW_CHROMIUM??`${homedir()}/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome`}},
  webServer:{command:`npx vite build && cd .. && ${process.env.LUFFY_PYTHON??"./venv/bin/python"} -m tests.owner_frontend_server 4176`,url:"http://127.0.0.1:4176/",reuseExistingServer:false,timeout:120000}});
