// Run after npm run check:webui with Playwright available (or PLAYWRIGHT_MODULE set), e.g.
// PLAYWRIGHT_MODULE=.../playwright/index.mjs PLAYWRIGHT_CHROMIUM_EXECUTABLE=... node tests/test_omni_gif_ui.mjs
// All /api/** requests are fulfilled by fixtures; nothing reaches a real upstream.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFileSync, mkdtempSync } from "node:fs";
import { resolve, join, extname } from "node:path";
import { tmpdir } from "node:os";

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const staticRoot = resolve("codex_image/webui/static");
const output = mkdtempSync(join(tmpdir(), "omni-gif-ui-"));
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
const ALL_MODELS = ["gpt-image-2", "gpt-image-2.5-flare", "gpt-image-2.5-sunburst", "gpt-image-2.6-flare", "gpt-image-2.6-sunburst"];

try {
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }, { width: 360, height: 780 }]) {
    const mobile = viewport.width <= 1024;
    const page = await browser.newPage({ viewport, locale: "zh-CN", isMobile: mobile, hasTouch: mobile });
    const errors = [];
    const submissions = [];
    const gifTask = {
      task_id: "fixture-gif", mode: "generate", status: "completed", prompt: "Dancing cat",
      created_at: "2026-11-06T01:00:00Z", updated_at: "2026-11-06T01:00:00Z",
      params: {
        model: "gpt-image-2.6-sunburst", main_model: "gpt-6-astra", quality: "low", size: "1024x576", n: 1,
        output_format: "gif", frame_count: 12, frame_duration_ms: 150, ratio: "16:9", orientation: "landscape",
      },
      outputs: [], input_files: [], gallery_refs: [],
    };
    const doneTask = (taskId, outputFormat) => ({
      task_id: taskId, mode: "generate", status: "completed", prompt: `Finished ${outputFormat}`,
      created_at: "2026-11-05T01:00:00Z", updated_at: "2026-11-05T01:00:00Z",
      params: { ...gifTask.params, output_format: outputFormat },
      output_urls: [`/api/tasks/${taskId}/outputs/1`],
      thumbnail_urls: [`/api/tasks/${taskId}/outputs/1/thumbnail`],
      outputs: [{ index: 1, status: "completed", url: `/api/tasks/${taskId}/outputs/1`, thumbnail_url: `/api/tasks/${taskId}/outputs/1/thumbnail`, format: outputFormat }],
      generated_count: 1, total_count: 1, input_files: [], gallery_refs: [],
    });
    const listedTasks = [doneTask("fixture-gif-done", "gif"), doneTask("fixture-png-done", "png")];
    page.on("pageerror", (err) => errors.push(err.message));
    await page.route("**/api/**", async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      let payload = {};
      if (path === "/api/health") payload = { ok: true, omni_poc: { enabled: true, image_model: "gpt-image-2" }, auth: { effective_source: "api", selected_source: "api", auth_available: true } };
      if (path === "/api/auth/session") payload = { authenticated: true, user: { id: 456, username: "GIF test" } };
      if (path === "/api/omni/keys") payload = { model: "gpt-image-2", keys: [{ id: "all", name: "All models", supported_image_models: ALL_MODELS }] };
      if (path === "/api/api-settings") payload = { settings: { image_model: "gpt-image-2", api_mode: "images" } };
      if (path === "/api/tasks" || path === "/api/tasks/recent") payload = { tasks: listedTasks };
      if (path === "/api/tasks/fixture-gif") payload = { task: gifTask };
      if (path.endsWith("/thumbnail") || /\/outputs\/\d+$/.test(path)) {
        await route.fulfill({ status: 404, body: "" });
        return;
      }
      if (path === "/api/queue") payload = { waiting: [], running: {}, channels: [] };
      if (path === "/api/generate") {
        const form = await new Response(request.postDataBuffer(), { headers: { "Content-Type": request.headers()["content-type"] } }).formData();
        const entries = Object.fromEntries(form);
        submissions.push(entries);
        payload = { task: { ...gifTask, task_id: `submitted-${submissions.length}`, status: "queued", params: { ...entries } } };
      }
      await route.fulfill({ json: payload });
    });
    await page.goto(base);
    await page.waitForFunction(() => document.querySelector("#omni-poc-key-select option[value=all]"));

    const model = page.locator("#omniImageModel");
    const trigger = page.locator("#omniImageModelTrigger");
    const menu = page.locator("#omniImageModelOptions");
    const chooseModel = async (value) => {
      await trigger.click();
      await menu.locator(`[data-image-model-option="${value}"]`).click();
      assert.equal(await model.inputValue(), value);
    };
    const gifButton = page.locator('#outputFormatGroup .radio-btn[data-val="gif"]');
    const format = page.locator("#outputFormat");
    const quality = page.locator("#quality");
    const qualityButton = (value) => page.locator(`.quality-field .radio-btn[data-val="${value}"]`);
    const gifField = page.locator("#gifSettingsField");
    const frameCount = page.locator("#gifFrameCount");
    const frameDuration = page.locator("#gifFrameDuration");
    const pixelPreview = page.locator("#pixelPreview");
    // Narrow screens: all four format buttons must be fully visible, untruncated and non-overlapping.
    const assertFormatButtonsFit = async (state) => {
      const layout = await page.evaluate(() => [...document.querySelectorAll("#outputFormatGroup .radio-btn")].map((button) => {
        const rect = button.getBoundingClientRect();
        const range = document.createRange();
        range.selectNodeContents(button);
        const text = range.getBoundingClientRect();
        const style = getComputedStyle(button);
        return {
          value: button.dataset.val, left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom,
          visible: rect.width > 0 && rect.height > 0,
          scrollWidth: button.scrollWidth, clientWidth: button.clientWidth,
          textInside: text.left >= rect.left + parseFloat(style.paddingLeft) - 1 && text.right <= rect.right - parseFloat(style.paddingRight) + 1,
          viewportInside: rect.left >= 0 && rect.right <= window.innerWidth,
        };
      }));
      assert.deepEqual(layout.map((item) => item.value), ["png", "jpeg", "webp", "gif"]);
      for (const item of layout) {
        assert.ok(item.visible && item.viewportInside, `${state}: ${item.value} must be visible inside the viewport`);
        assert.ok(item.scrollWidth <= item.clientWidth, `${state}: ${item.value} must not overflow (${item.scrollWidth} > ${item.clientWidth})`);
        assert.ok(item.textInside, `${state}: ${item.value} label must fit inside its padding box`);
      }
      for (let i = 0; i < layout.length; i += 1) {
        for (let j = i + 1; j < layout.length; j += 1) {
          const a = layout[i];
          const b = layout[j];
          const intersects = a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5;
          assert.equal(intersects, false, `${state}: ${a.value} and ${b.value} must not overlap`);
        }
      }
    };

    // Task cards mark GIF outputs with a small badge on the static first-frame thumbnail.
    await page.waitForFunction(() => document.querySelector('[data-task-id="fixture-gif-done"]'));
    assert.equal(await page.locator('[data-task-id="fixture-gif-done"] .task-thumb-format-badge').count(), 1);
    assert.equal(await page.locator('[data-task-id="fixture-png-done"] .task-thumb-format-badge').count(), 0);

    // Non-2.6 model: gif is greyed out with a hint and cannot be selected.
    assert.equal(await model.inputValue(), "gpt-image-2");
    assert.equal(await gifButton.getAttribute("aria-disabled"), "true");
    assert.match(await gifButton.getAttribute("title"), /2\.6/);
    await gifButton.click({ force: true });
    assert.equal(await format.inputValue(), "png", "disabled gif must not be selectable");
    assert.equal(await gifField.isVisible(), false);

    // Prepare static settings that GIF mode must override and later restore.
    await qualityButton("high").click();
    assert.equal(await quality.inputValue(), "high");
    await page.locator('#resolutionGroup .radio-btn[data-val="4k"]').click();

    if (mobile) await assertFormatButtonsFit("png selected, gif disabled");
    await chooseModel("gpt-image-2.6-flare");
    assert.equal(await gifButton.getAttribute("aria-disabled"), "false");
    if (mobile) await assertFormatButtonsFit("png selected, gif available");
    assert.equal(await gifButton.getAttribute("title"), null);
    await gifButton.click();
    assert.equal(await format.inputValue(), "gif");
    assert.equal(await gifField.isVisible(), true, "frame controls appear in GIF mode");
    if (mobile) await assertFormatButtonsFit("gif selected");
    assert.equal(await frameCount.inputValue(), "9");
    assert.equal(await frameDuration.inputValue(), "200");
    assert.equal(await page.locator(".resolution-field").isVisible(), false, "resolution tiers hidden in GIF mode");
    assert.equal(await page.locator(".custom-size-control").isVisible(), false, "size mode hidden in GIF mode");
    assert.equal(await page.locator(".ratio-field").isVisible(), true, "ratio stays selectable in GIF mode");
    assert.equal(await page.locator("#compression").isDisabled(), true, "compression disabled for gif");
    await gifButton.dblclick();
    assert.equal(await page.locator("#compressionPopover").isVisible(), false, "compression popover never opens for gif");
    assert.equal(await quality.inputValue(), "medium", "high quality falls back to medium");
    for (const value of ["auto", "high"]) assert.equal(await qualityButton(value).getAttribute("aria-disabled"), "true");
    for (const value of ["low", "medium"]) assert.equal(await qualityButton(value).getAttribute("aria-disabled"), "false");
    await qualityButton("high").click({ force: true });
    assert.equal(await quality.inputValue(), "medium", "blocked quality must not be selectable");
    await qualityButton("low").click();
    assert.equal(await quality.inputValue(), "low");
    assert.match(await pixelPreview.innerText(), /单帧 960×960/);
    assert.match(await pixelPreview.innerText(), /1\.8 秒/);

    // Layout: GIF controls stay inside the panel without overflow.
    await gifField.scrollIntoViewIfNeeded();
    const fieldBox = await gifField.boundingBox();
    const panelBox = await page.locator(".output-panel").boundingBox();
    assert.ok(fieldBox && panelBox && fieldBox.x >= panelBox.x - 1 && fieldBox.x + fieldBox.width <= panelBox.x + panelBox.width + 1, "gif field fits the output panel");
    for (const id of ["#gifFrameCount", "#gifFrameDuration"]) {
      const box = await page.locator(id).boundingBox();
      assert.ok(box && box.width >= 36 && box.x >= fieldBox.x - 1 && box.x + box.width <= fieldBox.x + fieldBox.width + 1, `${id} fits the gif field`);
    }
    const overflow = await page.evaluate(() => {
      const grid = document.querySelector("#settingsGrid");
      const gridRect = grid.getBoundingClientRect();
      const nodes = [...grid.querySelectorAll("#gifSettingsField, #gifSettingsField *, #outputFormatField, #outputFormatField .radio-btn, #pixelPreview, .orientation-field, .ratio-field, .quality-field")]
        .filter((node) => node.getClientRects().length);
      return {
        page: document.documentElement.scrollWidth > window.innerWidth + 1,
        outside: nodes.filter((node) => {
          const rect = node.getBoundingClientRect();
          return rect.left < gridRect.left - 1 || rect.right > gridRect.right + 1;
        }).map((node) => node.id || node.className),
        clipped: nodes.filter((node) => node.scrollWidth > node.clientWidth + 1).map((node) => node.id || node.className),
      };
    });
    assert.deepEqual(overflow, { page: false, outside: [], clipped: [] }, "no horizontal overflow in GIF mode");
    const overlaps = await page.evaluate(() => {
      const boxes = [...document.querySelectorAll("#settingsGrid > *")]
        .filter((node) => node.getClientRects().length && getComputedStyle(node).display !== "contents")
        .flatMap((node) => node.classList.contains("quantity-quality-row") ? [...node.children] : [node])
        .map((node) => ({ name: node.id || node.className, rect: node.getBoundingClientRect() }))
        .filter((item) => item.rect.width > 0 && item.rect.height > 0);
      const hits = [];
      for (let i = 0; i < boxes.length; i += 1) {
        for (let j = i + 1; j < boxes.length; j += 1) {
          const a = boxes[i].rect;
          const b = boxes[j].rect;
          if (a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1) hits.push(`${boxes[i].name} x ${boxes[j].name}`);
        }
      }
      return hits;
    });
    assert.deepEqual(overlaps, [], "settings fields must not overlap in GIF mode");
    await page.waitForTimeout(400);
    await page.locator(".output-panel").screenshot({ path: join(output, `${viewport.width}-gif-mode.png`) });
    await page.locator("#outputFormatField").scrollIntoViewIfNeeded();
    await page.locator("#pixelPreview, #outputFormatField, .moderation-field").last().screenshot({ path: join(output, `${viewport.width}-gif-moderation.png`) });
    await page.locator("#outputFormatField").screenshot({ path: join(output, `${viewport.width}-gif-format.png`) });

    // Frame-count driven sizing.
    await frameCount.fill("16");
    await frameCount.dispatchEvent("change");
    assert.match(await pixelPreview.innerText(), /720×720/);
    await frameCount.fill("40");
    await frameCount.dispatchEvent("change");
    assert.equal(await frameCount.inputValue(), "16", "frame count clamps to 16");
    await frameCount.fill("9");
    await frameCount.dispatchEvent("change");
    await frameDuration.fill("333");
    await frameDuration.dispatchEvent("change");
    assert.equal(await frameDuration.inputValue(), "330", "frame duration rounds to 10ms");
    await page.locator('#ratioGroup .radio-btn[data-val="16:9"]').click();
    assert.match(await pixelPreview.innerText(), /1024×576/);

    await page.locator("#promptEditor").click();
    await page.keyboard.type("A looping animation");
    await page.locator("#runButton").click();
    await page.waitForFunction(() => document.querySelector('[data-task-id="submitted-1"]'));
    const submitted = submissions[0];
    assert.equal(submitted.model, "gpt-image-2.6-flare");
    assert.equal(submitted.output_format, "gif");
    assert.equal(submitted.frame_count, "9");
    assert.equal(submitted.frame_duration_ms, "330");
    assert.equal(submitted.size, "1024x576");
    assert.equal(submitted.quality, "low");
    assert.equal(submitted.ratio, "16:9");
    assert.equal(submitted.resolution, undefined, "GIF submissions carry no resolution tier");
    assert.equal("output_compression" in submitted, false, "GIF never sends output_compression");
    assert.equal("complexity" in submitted, false, "the frontend does not send complexity");

    // Leaving GIF by switching to a non-2.6 model restores png and the static settings.
    await chooseModel("gpt-image-2.5-sunburst");
    assert.equal(await format.inputValue(), "png", "non-2.6 model falls back to png");
    assert.equal(await gifButton.getAttribute("aria-disabled"), "true");
    assert.equal(await gifField.isVisible(), false);
    assert.equal(await page.locator(".resolution-field").isVisible(), true);
    assert.equal(await page.locator("#resolution").inputValue(), "4k", "resolution tier restored");
    assert.equal(await quality.inputValue(), "high", "quality restored after GIF mode");
    assert.equal(await qualityButton("high").getAttribute("aria-disabled"), "false");
    assert.match(await pixelPreview.innerText(), /输出像素/);

    // Non-gif submission carries no frame parameters.
    await page.locator("#runButton").click();
    await page.waitForFunction(() => document.querySelector('[data-task-id="submitted-2"]'));
    assert.equal(submissions[1].output_format, "png");
    assert.equal("frame_count" in submissions[1], false);
    assert.equal("frame_duration_ms" in submissions[1], false);

    // History restore brings back the GIF parameters.
    await page.evaluate((task) => localStorage.setItem("codex-image-history-task-reuse-handoff", JSON.stringify({ intent: "view", task })), gifTask);
    await page.reload();
    await page.waitForFunction(() => document.querySelector("#omni-poc-key-select option[value=all]"));
    await page.waitForFunction(() => document.querySelector("#outputFormat").value === "gif");
    assert.equal(await model.inputValue(), "gpt-image-2.6-sunburst");
    assert.equal(await frameCount.inputValue(), "12");
    assert.equal(await frameDuration.inputValue(), "150");
    assert.equal(await page.locator("#ratio").inputValue(), "16:9");
    assert.equal(await quality.inputValue(), "low");
    assert.equal(await gifField.isVisible(), true);
    assert.match(await pixelPreview.innerText(), /1024×576/);
    await page.locator("#runButton").click();
    await page.waitForFunction(() => document.querySelector('[data-task-id="submitted-3"]'));
    assert.equal(submissions[2].output_format, "gif");
    assert.equal(submissions[2].frame_count, "12");
    assert.equal(submissions[2].frame_duration_ms, "150");
    assert.equal(submissions[2].size, "1024x576");

    // Leaving GIF via the format buttons also restores the default quality chosen before.
    await page.locator('#outputFormatGroup .radio-btn[data-val="png"]').click();
    assert.equal(await gifField.isVisible(), false);
    assert.equal(await qualityButton("auto").getAttribute("aria-disabled"), "false");
    if (mobile) await assertFormatButtonsFit("png restored");

    // Static formats keep the compression popover entry (double-click jpeg/webp).
    await page.locator('#outputFormatGroup .radio-btn[data-val="jpeg"]').dblclick();
    assert.equal(await format.inputValue(), "jpeg");
    const popover = page.locator("#compressionPopover");
    assert.equal(await popover.isVisible(), true, "compression popover still opens for jpeg");
    const popoverBox = await popover.boundingBox();
    assert.ok(popoverBox && popoverBox.x >= 0 && popoverBox.x + popoverBox.width <= viewport.width + 1, "compression popover stays inside the viewport");
    assert.equal(await page.locator("#compression").isDisabled(), false);
    await page.keyboard.press("Escape");

    assert.deepEqual(errors, []);
    console.log(`PASS ${viewport.width}px: gif gating, frame controls, quality/compression/resolution rules, FormData, model fallback, history restore, layout`);
    await page.close();
  }
  console.log(`Screenshots: ${output}`);
} finally {
  await browser.close();
  await new Promise((done) => server.close(done));
}
