import { getLegacyBridge } from "./state";

const SELECTED_KEY_STORAGE = "ilab.omniSelectedKeyId";
const LOGIN_URL = "https://api.brislouise.online/image-generator";

interface OmniUser {
  id: number;
  email?: string;
  username?: string;
  balance?: number;
}

interface OmniKey {
  id: string;
  name: string;
  group_name?: string;
  masked_key?: string;
  supports_title_model?: boolean;
}

let enabled = false;
let authenticated = false;
let selectedKeyId = window.localStorage.getItem(SELECTED_KEY_STORAGE)?.trim() || "";
let keys: OmniKey[] = [];
let user: OmniUser | null = null;
let sessionConnectionOk = true;

export function isOmniPocMode(): boolean {
  return enabled || document.documentElement.classList.contains("omni-poc-mode");
}

export function getSelectedOmniKeyId(): string {
  return selectedKeyId;
}

export function omniHeaders(): Record<string, string> {
  return {};
}

export function requireOmniApiKeyBeforeSubmit(): void {
  if (!isOmniPocMode()) return;
  if (!authenticated) {
    throw new Error("请先从 Omni 主站登录后再使用生图功能");
  }
  if (!keys.length) {
    throw new Error("没有检测到可调用 gpt-image-2 的 API Key");
  }
}

export function updateOmniLegacyAuthState(): void {
  const bridge = getLegacyBridge();
  const ready = Boolean(authenticated && keys.length);
  bridge.state.authAvailable = ready;
  bridge.state.authStatus = {
    selected_source: "api",
    effective_source: "api",
    auth_available: ready,
    sources: {},
  };
  if (bridge.els.apiStatus) {
    bridge.els.apiStatus.className = `status-dot ${sessionConnectionOk ? "ok" : "error"}`;
  }
  if (bridge.els.runButton) {
    bridge.els.runButton.disabled = !ready;
  }
  if (bridge.els.authSourceDetail) {
    const text = ready ? "Omni API Key" : authenticated ? "没有可用 Omni API Key" : "请从 Omni 主站登录";
    bridge.els.authSourceDetail.textContent = text;
    bridge.els.authSourceDetail.title = text;
  }
}

function mountPoint(): Element {
  return document.querySelector(".nav-actions") || document.querySelector("header") || document.body;
}

function labelForKey(key: OmniKey): string {
  const group = key.group_name ? ` · ${key.group_name}` : "";
  const mask = key.masked_key ? ` · ${key.masked_key}` : "";
  const title = key.supports_title_model ? " · 支持标题" : "";
  return `${key.name || "Omni API Key"}${group}${mask}${title}`;
}

function renderKeyOptions(select: HTMLSelectElement): void {
  select.innerHTML = "";
  if (!keys.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = authenticated ? "没有可用的 gpt-image-2 API Key" : "请先登录";
    select.appendChild(option);
    select.value = "";
    selectedKeyId = "";
    window.localStorage.removeItem(SELECTED_KEY_STORAGE);
    return;
  }
  const autoOption = document.createElement("option");
  autoOption.value = "";
  autoOption.textContent = "自动选择（推荐）";
  select.appendChild(autoOption);
  keys.forEach((key) => {
    const option = document.createElement("option");
    option.value = key.id;
    option.textContent = labelForKey(key);
    select.appendChild(option);
  });
  if (selectedKeyId && !keys.some((key) => key.id === selectedKeyId)) {
    selectedKeyId = "";
  }
  select.value = selectedKeyId;
}

function renderSession(root: HTMLElement): void {
  const status = root.querySelector<HTMLSpanElement>(".omni-poc-key-status");
  const account = root.querySelector<HTMLSpanElement>(".omni-poc-account");
  const select = root.querySelector<HTMLSelectElement>(".omni-poc-key-select");
  const login = root.querySelector<HTMLAnchorElement>(".omni-poc-login-link");
  const refresh = root.querySelector<HTMLButtonElement>('[data-action="refresh"]');
  if (account) {
    account.textContent = authenticated && user
      ? `${user.username || user.email || `用户 ${user.id}`} · 余额 ${Number(user.balance || 0).toFixed(2)}`
      : "未登录 Omni 主站";
  }
  if (login) {
    login.classList.toggle("hidden", authenticated);
  }
  if (select) {
    select.disabled = !authenticated || !keys.length;
    select.classList.toggle("hidden", !authenticated);
    renderKeyOptions(select);
  }
  if (refresh) {
    refresh.disabled = false;
    refresh.classList.toggle("hidden", !authenticated);
  }
  if (status) {
    status.textContent = authenticated ? (keys.length ? "" : "没有检测到可调用 gpt-image-2 的 API Key") : "";
    status.classList.toggle("hidden", authenticated && keys.length > 0);
  }
  updateOmniLegacyAuthState();
}

async function refreshSessionAndKeys(root: HTMLElement): Promise<void> {
  const status = root.querySelector<HTMLSpanElement>(".omni-poc-key-status");
  const refresh = root.querySelector<HTMLButtonElement>('[data-action="refresh"]');
  if (status) status.textContent = "正在读取登录状态";
  if (refresh) refresh.disabled = true;
  try {
    const sessionResponse = await fetch("/api/auth/session", { credentials: "include" });
    const sessionPayload = await sessionResponse.json().catch(() => ({}));
    sessionConnectionOk = sessionResponse.ok;
    authenticated = Boolean(sessionPayload?.authenticated);
    user = authenticated ? sessionPayload.user || null : null;
    keys = [];
    if (authenticated) {
      const keysResponse = await fetch("/api/omni/keys", { credentials: "include" });
      const keysPayload = await keysResponse.json().catch(() => ({}));
      sessionConnectionOk = sessionConnectionOk && keysResponse.ok;
      keys = Array.isArray(keysPayload?.keys) ? keysPayload.keys : [];
    }
  } catch {
    sessionConnectionOk = false;
    authenticated = false;
    user = null;
    keys = [];
    if (status) status.textContent = "登录状态读取失败";
  } finally {
    renderSession(root);
  }
}

function renderKeyControl(): void {
  if (document.querySelector(".omni-poc-key-control")) return;
  const root = document.createElement("div");
  root.className = "omni-poc-key-control";
  root.innerHTML = `
    <label class="omni-poc-key-label" for="omni-poc-key-select">Omni API Key</label>
    <span class="omni-poc-account"></span>
    <select id="omni-poc-key-select" class="omni-poc-key-select"></select>
    <button class="omni-poc-key-button" type="button" data-action="refresh">刷新</button>
    <a class="omni-poc-key-button omni-poc-login-link" href="${LOGIN_URL}">登录 Omni</a>
    <span class="omni-poc-key-status" aria-live="polite"></span>
  `;
  mountPoint().appendChild(root);

  root.addEventListener("click", (event) => {
    const target = event.target as HTMLElement;
    if (target.dataset.action === "refresh") {
      void refreshSessionAndKeys(root);
    }
  });
  root.querySelector<HTMLSelectElement>(".omni-poc-key-select")?.addEventListener("change", (event) => {
    selectedKeyId = (event.target as HTMLSelectElement).value;
    if (selectedKeyId) {
      window.localStorage.setItem(SELECTED_KEY_STORAGE, selectedKeyId);
    } else {
      window.localStorage.removeItem(SELECTED_KEY_STORAGE);
    }
    updateOmniLegacyAuthState();
  });
  void refreshSessionAndKeys(root);
}

export async function initOmniPocKeyControl(): Promise<void> {
  try {
    const response = await fetch("/api/health");
    const data = await response.json();
    if (!data?.omni_poc?.enabled) return;
    enabled = true;
    document.documentElement.classList.add("omni-poc-mode");
    renderKeyControl();
    updateOmniLegacyAuthState();
  } catch {
    return;
  }
}
