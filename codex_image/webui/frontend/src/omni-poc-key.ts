import { getLegacyBridge } from "./state";

const STORAGE_KEY = "ilab.omniApiKey";

let enabled = false;
let sourceUrl = "https://github.com/brislouise/ilab-gpt-conjure-poc";

export function isOmniPocMode(): boolean {
  return enabled || document.documentElement.classList.contains("omni-poc-mode");
}

export function getOmniApiKey(): string {
  return window.localStorage.getItem(STORAGE_KEY)?.trim() || "";
}

export function setOmniApiKey(value: string): void {
  const clean = value.trim();
  if (clean) {
    window.localStorage.setItem(STORAGE_KEY, clean);
  } else {
    window.localStorage.removeItem(STORAGE_KEY);
  }
}

export function maskOmniApiKey(value: string): string {
  const clean = value.trim();
  if (!clean) return "";
  if (clean.length <= 8) return "********";
  return `${clean.slice(0, 3)}...${clean.slice(-4)}`;
}

export function omniHeaders(): Record<string, string> {
  const apiKey = getOmniApiKey();
  return isOmniPocMode() && apiKey ? { "X-Omni-API-Key": apiKey } : {};
}

export function requireOmniApiKeyBeforeSubmit(): void {
  if (isOmniPocMode() && !getOmniApiKey()) {
    throw new Error("请先填写 Omni API Key");
  }
}

function updateLegacyAuthState(): void {
  const bridge = getLegacyBridge();
  bridge.state.authAvailable = true;
  bridge.state.authStatus = {
    selected_source: "api",
    effective_source: "api",
    auth_available: true,
    sources: {},
  };
  if (bridge.els.apiStatus) {
    bridge.els.apiStatus.className = "status-dot ok";
  }
  if (bridge.els.runButton) {
    bridge.els.runButton.disabled = false;
  }
}

function mountPoint(): Element {
  const bridge = getLegacyBridge();
  return bridge.els.authSourceGroup?.parentElement || document.querySelector("header") || document.body;
}

function renderKeyControl(): void {
  if (document.querySelector(".omni-poc-key-control")) return;
  const root = document.createElement("div");
  root.className = "omni-poc-key-control";
  root.innerHTML = `
    <label class="omni-poc-key-label" for="omni-poc-key-input">Omni API Key</label>
    <input id="omni-poc-key-input" class="omni-poc-key-input" type="password" autocomplete="off" placeholder="sk-..." />
    <button class="omni-poc-key-button" type="button" data-action="save">保存</button>
    <button class="omni-poc-key-button" type="button" data-action="clear">清除</button>
    <span class="omni-poc-key-status" aria-live="polite"></span>
    <span class="omni-poc-key-notice">Key 保存在本浏览器，仅在验证和提交任务时发送到 POC 后端。</span>
    <a class="omni-poc-source-link" href="${sourceUrl}" target="_blank" rel="noreferrer">源码</a>
  `;
  mountPoint().appendChild(root);

  const input = root.querySelector<HTMLInputElement>("#omni-poc-key-input");
  const status = root.querySelector<HTMLSpanElement>(".omni-poc-key-status");
  const current = getOmniApiKey();
  if (input && current) input.value = current;
  if (status && current) status.textContent = `已保存 ${maskOmniApiKey(current)}`;

  root.addEventListener("click", async (event) => {
    const target = event.target as HTMLElement;
    const action = target.dataset.action;
    if (!action || !input || !status) return;
    if (action === "clear") {
      setOmniApiKey("");
      input.value = "";
      status.textContent = "已清除";
      updateLegacyAuthState();
      return;
    }
    const value = input.value.trim();
    if (!value) {
      status.textContent = "请输入 Omni API Key";
      return;
    }
    setOmniApiKey(value);
    status.textContent = "验证中";
    const response = await fetch("/api/omni/validate", {
      method: "POST",
      headers: omniHeaders(),
    });
    if (response.ok) {
      status.textContent = `可用 ${maskOmniApiKey(value)}`;
      updateLegacyAuthState();
    } else {
      const payload = await response.json().catch(() => ({}));
      status.textContent = String(payload.detail || "验证失败");
    }
  });
}

export async function initOmniPocKeyControl(): Promise<void> {
  try {
    const response = await fetch("/api/health");
    const data = await response.json();
    if (!data?.omni_poc?.enabled) return;
    enabled = true;
    sourceUrl = String(data.omni_poc.source_url || sourceUrl);
    document.documentElement.classList.add("omni-poc-mode");
    renderKeyControl();
    updateLegacyAuthState();
  } catch {
    return;
  }
}
