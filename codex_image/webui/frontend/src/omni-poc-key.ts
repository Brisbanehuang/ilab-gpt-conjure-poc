import { getLegacyBridge } from "./state";
import { safeJson } from "./api";
import { formatTranslation, LOCALE_CHANGE_EVENT, translate } from "./i18n";

const SELECTED_KEY_STORAGE = "ilab.omniSelectedKeyId";
const SELECTED_MODEL_STORAGE = "ilab.omniImageModel";
const IMAGE_MODELS = ["gpt-image-2", "gpt-image-2.5-flare", "gpt-image-2.5-sunburst"];
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
  supported_image_models: string[];
  model_lookup_failed?: boolean;
}

let enabled = false;
let authenticated = false;
let selectedKeyId = window.localStorage.getItem(SELECTED_KEY_STORAGE)?.trim() || "";
let keys: OmniKey[] = [];
let user: OmniUser | null = null;
let sessionConnectionOk = true;
let selectedImageModel = "gpt-image-2";
let refreshing = false;
let modelChosenBeforeSession = false;

export function currentOmniImageModel(): string {
  return selectedImageModel;
}

export function setOmniImageModel(model: string): void {
  if (!user) modelChosenBeforeSession = true;
  // Preserve an unavailable historical model instead of silently replacing it.
  selectedImageModel = model;
  const select = document.querySelector<HTMLSelectElement>("#omniImageModel");
  if (select) {
    select.querySelectorAll('[data-unavailable-model]').forEach((option) => option.remove());
    if (!IMAGE_MODELS.includes(model)) {
      const option = new Option(model, model);
      option.dataset.unavailableModel = "true";
      option.disabled = true;
      select.appendChild(option);
    }
    select.value = model;
  }
  if (user && IMAGE_MODELS.includes(model)) {
    localStorage.setItem(`${SELECTED_MODEL_STORAGE}.${user.id}`, model);
  }
  const root = document.querySelector<HTMLElement>(".omni-poc-key-control");
  if (root) renderSession(root);
}

function compatibleKeys(): OmniKey[] {
  return keys.filter((key) => !key.model_lookup_failed && key.supported_image_models?.includes(selectedImageModel));
}

function modelSelectionError(): string {
  if (!IMAGE_MODELS.includes(selectedImageModel)) return translate("omni.modelUnavailable");
  if (!sessionConnectionOk) return translate("omni.lookupFailed");
  const selected = keys.find((key) => key.id === selectedKeyId);
  if (selectedKeyId && selected?.model_lookup_failed) return translate("omni.lookupFailed");
  if (selectedKeyId && !compatibleKeys().some((key) => key.id === selectedKeyId)) {
    return formatTranslation("omni.selectedKeyUnsupported", { model: selectedImageModel });
  }
  if (!compatibleKeys().length) {
    return keys.some((key) => key.model_lookup_failed)
      ? translate("omni.lookupFailed")
      : formatTranslation("omni.noModelKey", { model: selectedImageModel });
  }
  return "";
}

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
    throw new Error(translate("omni.loginRequired"));
  }
  if (refreshing) throw new Error(translate("omni.loading"));
  const error = modelSelectionError();
  if (error) throw new Error(error);
}

export function updateOmniLegacyAuthState(): void {
  const bridge = getLegacyBridge();
  const ready = Boolean(authenticated && !refreshing && !modelSelectionError());
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
    const text = ready ? "Omni API Key" : authenticated ? modelSelectionError() : translate("omni.loginRequired");
    bridge.els.authSourceDetail.textContent = text;
    bridge.els.authSourceDetail.title = text;
  }
}

function mountPoint(): Element {
  return document.querySelector(".nav-actions") || document.querySelector("header") || document.body;
}

function labelForKey(key: OmniKey): string {
  const group = key.group_name ? ` · ${key.group_name}` : "";
  return `${key.name || "Omni API Key"}${group}`;
}

function renderKeyOptions(select: HTMLSelectElement): void {
  select.innerHTML = "";
  const candidates = compatibleKeys();
  if (!candidates.length && !selectedKeyId) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = authenticated ? translate("omni.noKey") : translate("omni.login");
    select.appendChild(option);
    select.value = "";
    return;
  }
  const autoOption = document.createElement("option");
  autoOption.value = "";
  autoOption.textContent = translate("omni.autoKey");
  autoOption.disabled = !candidates.length;
  select.appendChild(autoOption);
  candidates.forEach((key) => {
    const option = document.createElement("option");
    option.value = key.id;
    option.textContent = labelForKey(key);
    select.appendChild(option);
  });
  if (selectedKeyId && !candidates.some((key) => key.id === selectedKeyId)) {
    const selected = keys.find((key) => key.id === selectedKeyId);
    const option = new Option(selected ? labelForKey(selected) : translate("omni.selectedKeyUnavailable"), selectedKeyId);
    option.disabled = true;
    select.appendChild(option);
  }
  select.value = selectedKeyId;
}

function renderSession(root: HTMLElement): void {
  const status = document.querySelector<HTMLSpanElement>("#omniModelStatus");
  const account = root.querySelector<HTMLSpanElement>(".omni-poc-account");
  const select = root.querySelector<HTMLSelectElement>(".omni-poc-key-select");
  const login = root.querySelector<HTMLAnchorElement>(".omni-poc-login-link");
  const refresh = root.querySelector<HTMLButtonElement>('[data-action="refresh"]');
  if (account) {
    const accountText = authenticated && user
      ? `${user.username || user.email || `用户 ${user.id}`} · 余额 ${Number(user.balance || 0).toFixed(2)}`
      : "未登录 Omni 主站";
    account.textContent = "";
    account.title = accountText;
  }
  if (login) {
    login.classList.toggle("hidden", authenticated);
    login.textContent = translate("omni.login");
  }
  if (select) {
    select.disabled = !authenticated || refreshing || !compatibleKeys().length;
    select.classList.toggle("hidden", !authenticated);
    renderKeyOptions(select);
  }
  if (refresh) {
    refresh.disabled = refreshing;
    refresh.textContent = translate("omni.refresh");
    refresh.classList.toggle("hidden", !authenticated);
  }
  if (status) {
    const error = !sessionConnectionOk ? translate("omni.lookupFailed") : authenticated ? modelSelectionError() : "";
    status.textContent = refreshing ? translate("omni.loading") : error || (keys.some((key) => key.model_lookup_failed) ? translate("omni.partialLookupFailed") : "");
    status.title = status.textContent;
    status.classList.toggle("hidden", !status.textContent);
  }
  updateOmniLegacyAuthState();
}

async function refreshSessionAndKeys(root: HTMLElement): Promise<void> {
  if (refreshing) return;
  refreshing = true;
  renderSession(root);
  try {
    const sessionResponse = await fetch("/api/auth/session", { credentials: "include" });
    const sessionPayload = await safeJson(sessionResponse);
    if (!sessionResponse.ok) throw new Error("session lookup failed");
    sessionConnectionOk = true;
    const previousUserId = user?.id;
    authenticated = Boolean(sessionPayload?.authenticated);
    user = authenticated ? sessionPayload.user || null : null;
    if (user?.id !== previousUserId) {
      const savedModel = user ? localStorage.getItem(`${SELECTED_MODEL_STORAGE}.${user.id}`) : null;
      const initialModel = !previousUserId && user && modelChosenBeforeSession
        ? selectedImageModel
        : savedModel && IMAGE_MODELS.includes(savedModel) ? savedModel : "gpt-image-2";
      setOmniImageModel(initialModel);
      modelChosenBeforeSession = false;
    }
    keys = [];
    if (authenticated) {
      const keysResponse = await fetch("/api/omni/keys", { credentials: "include" });
      const keysPayload = await safeJson(keysResponse);
      if (!keysResponse.ok || !Array.isArray(keysPayload?.keys)) throw new Error("key lookup failed");
      keys = keysPayload.keys;
    }
  } catch {
    sessionConnectionOk = false;
    keys = [];
  } finally {
    refreshing = false;
    renderSession(root);
    getLegacyBridge().methods.updateRequestPreview?.();
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
  `;
  mountPoint().appendChild(root);
  const modelSelect = document.querySelector<HTMLSelectElement>("#omniImageModel");
  modelSelect?.addEventListener("change", () => {
    setOmniImageModel(modelSelect.value);
    getLegacyBridge().methods.updateRequestPreview?.();
  });
  document.addEventListener(LOCALE_CHANGE_EVENT, () => renderSession(root));

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
    renderSession(root);
  });
  void refreshSessionAndKeys(root);
}

export async function initOmniPocKeyControl(): Promise<void> {
  try {
    const response = await fetch("/api/health");
    const data = await safeJson(response);
    if (!data?.omni_poc?.enabled) return;
    enabled = true;
    document.documentElement.classList.add("omni-poc-mode");
    renderKeyControl();
    updateOmniLegacyAuthState();
  } catch {
    return;
  }
}
