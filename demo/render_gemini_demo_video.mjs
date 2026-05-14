import { writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import playwright from "/Users/priyabhasin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.js";

const { chromium } = playwright;

const cwd = process.cwd();
const htmlPath = resolve(cwd, "demo/gemini_proactive_agent_demo.html");
const outputPath = resolve(cwd, "demo/gemini_proactive_agent_demo.webm");

const browser = await chromium.launch({
  headless: true,
  executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  args: ["--autoplay-policy=no-user-gesture-required"],
});

try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1 });
  await page.goto(`file://${htmlPath}`);
  await page.waitForFunction(() => typeof window.renderDemo === "function");
  const bytes = await page.evaluate(() => window.renderDemo());
  await writeFile(outputPath, Buffer.from(bytes));
  console.log(outputPath);
} finally {
  await browser.close();
}
