// Run after npm run check:webui with Playwright available (or PLAYWRIGHT_MODULE set).
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFileSync, mkdtempSync } from "node:fs";
import { resolve, join, extname } from "node:path";
import { tmpdir } from "node:os";

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const staticRoot = resolve("codex_image/webui/static");
const output = mkdtempSync(join(tmpdir(), "omni-models-ui-"));
const server = createServer((req, res) => {
  const path = new URL(req.url, "http://localhost").pathname;
  try {
    const file = join(staticRoot, path === "/" ? "index.html" : path.replace(/^\/static\//, ""));
    res.setHeader("Content-Type", { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" }[extname(file)] || "application/octet-stream");
    res.end(readFileSync(file));
  } catch {
    res.writeHead(404).end();
  }
});
await new Promise((done) => server.listen(0, "127.0.0.1", done));
const base = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE });
try {
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    const page = await browser.newPage({ viewport, locale: "zh-CN" });
    const errors = [];
    const submissions = [];
    let keyLookups = 0;
    let providerWrites = 0;
    let failModels = false;
    const oldTask = {
      task_id: "fixture-old", mode: "generate", status: "failed", prompt: "Historical image prompt",
      created_at: "2026-09-09T01:00:00Z", updated_at: "2026-09-09T01:00:00Z",
      params: { model: "gpt-image-2", main_model: "gpt-6-astra", quality: "auto", size: "2048x2048", n: 1 },
      outputs: [], input_files: [], gallery_refs: [], error: "Fixture failure",
    };
    page.on("pageerror", (err) => errors.push(err.message));
    await page.route("**/api/**", async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      let payload = {};
      if (path === "/api/health") payload = { ok: true, omni_poc: { enabled: true, image_model: "gpt-image-2" }, auth: { effective_source: "api", selected_source: "api", auth_available: true } };
      if (path === "/api/auth/session") payload = { authenticated: true, user: { id: 123, username: "UI test" } };
      if (path === "/api/omni/keys") {
        keyLookups++;
        payload = { model: "gpt-image-2", keys: [
          { id: "old", name: "Image 2 key", supported_image_models: failModels ? [] : ["gpt-image-2"], model_lookup_failed: failModels },
          { id: "new", name: "Image 2.5 key", supported_image_models: failModels ? [] : ["gpt-image-2.5-flare", "gpt-image-2.5-sunburst"], model_lookup_failed: failModels },
        ] };
      }
      if (path === "/api/api-settings") {
        if (request.method() !== "GET") providerWrites++;
        payload = { settings: { image_model: "gpt-image-2", api_mode: "images" } };
      }
      if (path === "/api/tasks" || path === "/api/tasks/recent") payload = { tasks: [oldTask] };
      if (path === "/api/tasks/fixture-old") payload = { task: oldTask };
      if (path === "/api/queue") payload = { waiting: [], running: {}, channels: [] };
      if (path === "/api/generate") {
        const form = await new Response(request.postDataBuffer(), { headers: { "Content-Type": request.headers()["content-type"] } }).formData();
        submissions.push(Object.fromEntries(form));
        payload = { task: { ...oldTask, task_id: `submitted-${submissions.length}`, status: "queued", params: { ...oldTask.params, model: form.get("model") } } };
      }
      await route.fulfill({ json: payload });
    });
    await page.goto(base);
    await page.waitForFunction(() => document.querySelector("#omni-poc-key-select option[value=old]"));
    const model = page.locator("#omniImageModel");
    const trigger = page.locator("#omniImageModelTrigger");
    const menu = page.locator("#omniImageModelOptions");
    const chooseModel = async (value) => {
      await trigger.click();
      await menu.locator(`[data-image-model-option="${value}"]`).click();
      assert.equal(await menu.isVisible(), false);
      assert.equal(await model.inputValue(), value);
    };
    const key = page.locator("#omni-poc-key-select");
    assert.deepEqual(await model.locator("option").evaluateAll((items) => items.map((item) => item.value)), ["gpt-image-2", "gpt-image-2.5-flare", "gpt-image-2.5-sunburst"]);
    assert.equal(await model.isVisible(), false, "native model select must not be displayed");
    await trigger.focus();
    await trigger.press("ArrowDown");
    assert.equal(await trigger.getAttribute("aria-expanded"), "true");
    await trigger.press("ArrowDown");
    await trigger.press("Enter");
    assert.equal(await model.inputValue(), "gpt-image-2.5-flare");
    assert.equal(await page.locator("#omniImageModelValue").innerText(), "GPT Image 2.5 Flare");
    await trigger.press("Space");
    await trigger.press("Home");
    await trigger.press("Enter");
    assert.equal(await model.inputValue(), "gpt-image-2");
    await trigger.click();
    await trigger.press("End");
    await trigger.press("Escape");
    assert.equal(await model.inputValue(), "gpt-image-2", "Escape must not commit the highlighted option");
    assert.equal(await menu.isVisible(), false);
    await trigger.click();
    await trigger.press("Tab");
    assert.equal(await menu.isVisible(), false);
    await key.selectOption("old");
    await chooseModel("gpt-image-2.5-flare");
    assert.equal(await key.inputValue(), "old", "manual keys must not silently change");
    assert.match(await page.locator(".omni-poc-key-status").innerText(), /无法调用/);
    assert.equal(await page.locator("#runButton").isDisabled(), true);
    await key.selectOption("");
    assert.deepEqual(await key.locator("option").evaluateAll((items) => items.map((item) => item.value)), ["", "new"]);
    const lookupsBeforeSwitch = keyLookups;
    await chooseModel("gpt-image-2.5-sunburst");
    assert.equal(keyLookups, lookupsBeforeSwitch, "model changes must reuse the key catalog");
    await page.evaluate((task) => localStorage.setItem("codex-image-history-task-reuse-handoff", JSON.stringify({ intent: "view", task })), oldTask);
    await page.reload();
    await page.waitForFunction(() => document.querySelector("#omni-poc-key-select option[value=old]"));
    await page.waitForFunction(() => document.querySelector("#omniImageModel").value === "gpt-image-2");
    assert.equal(await page.locator("#omniImageModelValue").innerText(), "GPT Image 2");
    await page.locator("#runButton").click();
    await page.waitForFunction(() => document.querySelector('[data-task-id="submitted-1"]'));
    assert.equal(submissions[0].model, "gpt-image-2", "history restore must change the submitted model");
    await chooseModel("gpt-image-2.5-sunburst");
    await page.locator("#runButton").click();
    await page.waitForFunction(() => document.querySelector('[data-task-id="submitted-2"]'));
    assert.equal(submissions[1].model, "gpt-image-2.5-sunburst");
    await page.reload();
    await page.waitForFunction(() => document.querySelector("#omni-poc-key-select option[value=new]"));
    assert.equal(await model.inputValue(), "gpt-image-2.5-sunburst");
    assert.equal(await page.locator("#omniImageModelValue").innerText(), "GPT Image 2.5 Sunburst");
    await page.locator('[data-omni-locale="en"]').click();
    assert.equal(await page.locator('[data-i18n="omni.imageModel"]').innerText(), "Image model");
    await trigger.scrollIntoViewIfNeeded();
    const box = await trigger.boundingBox();
    assert.ok(box && box.width >= 200 && box.x >= 0 && box.x + box.width <= viewport.width);
    await trigger.click();
    const menuBox = await menu.boundingBox();
    assert.ok(menuBox && menuBox.x >= 0 && menuBox.x + menuBox.width <= viewport.width);
    const appearance = await page.evaluate(() => {
      const styles = (id) => {
        const value = getComputedStyle(document.getElementById(id));
        return [value.backgroundColor, value.border, value.borderRadius, value.boxShadow, value.fontFamily, value.fontSize];
      };
      return { image: styles("omniImageModelOptions"), main: styles("mainModelOptions") };
    });
    assert.deepEqual(appearance.image, appearance.main, "image dropdown must reuse main-model menu styling");
    await page.locator(".output-panel").screenshot({ path: join(output, `${viewport.width}-image-dropdown.png`) });
    await page.locator("#omniImageModelLabel").click();
    assert.equal(await menu.isVisible(), false, "clicking outside must close the image dropdown");
    await page.locator("#mainModel").click();
    await page.locator("#mainModelOptions").screenshot({ path: join(output, `${viewport.width}-main-dropdown.png`) });
    await page.locator("#mainModel").press("Escape");
    failModels = true;
    await page.locator('.omni-poc-key-control [data-action="refresh"]').click();
    await page.waitForFunction(() => document.querySelector(".omni-poc-key-status").textContent.includes("Could not load"));
    assert.equal(await page.locator("#runButton").isDisabled(), true);
    assert.equal(providerWrites, 0, "model selection must never write shared provider settings");
    assert.deepEqual(errors, []);
    console.log(`PASS ${viewport.width}px: styled dropdown, keyboard, dismissal, model/key selection, history reuse, submit, persistence, i18n, lookup errors, no provider writes`);
    await page.close();
  }
  console.log(`Screenshots: ${output}`);
} finally {
  await browser.close();
  await new Promise((done) => server.close(done));
}
