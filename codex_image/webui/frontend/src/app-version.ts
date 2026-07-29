import { formatTranslation, LOCALE_CHANGE_EVENT, translate } from "./i18n";
import { getLegacyBridge } from "./state";

interface AppVersionPayload {
  current_version_label?: string;
  latest_version_label?: string;
  source?: "portable" | "source" | string;
  update_available?: boolean;
  release_url?: string;
  updater_available?: boolean;
}

let appVersionInitialized = false;
let payload: AppVersionPayload | null = null;

function els() {
  return getLegacyBridge().els;
}

function setModalHidden(hidden: boolean): void {
  const modal = els().versionModal as HTMLElement | null;
  if (!modal) return;
  modal.classList.toggle("hidden", hidden);
  modal.setAttribute("aria-hidden", hidden ? "true" : "false");
}

function renderAppVersion(statusText?: string): void {
  const versionInfo = els().versionInfo as HTMLButtonElement | null;
  const versionLabel = els().versionLabel as HTMLElement | null;
  const badge = els().versionUpdateBadge as HTMLElement | null;
  const current = els().versionCurrent as HTMLElement | null;
  const latest = els().versionLatest as HTMLElement | null;
  const source = els().versionSource as HTMLElement | null;
  const releaseLink = els().versionReleaseLink as HTMLAnchorElement | null;
  const updateButton = els().versionUpdateButton as HTMLButtonElement | null;
  const modalStatus = els().versionModalStatus as HTMLElement | null;
  const currentLabel = payload?.current_version_label || "...";
  const latestLabel = payload?.latest_version_label || currentLabel;
  const updateAvailable = Boolean(payload?.update_available);
  const updateAvailableText = formatTranslation("footer.updateAvailable", { version: latestLabel });

  if (versionLabel) {
    versionLabel.textContent = payload ? formatTranslation("footer.version", { version: currentLabel }) : translate("footer.versionLoading");
  }
  if (versionInfo) {
    versionInfo.classList.toggle("has-update", updateAvailable);
    versionInfo.title = updateAvailable ? updateAvailableText : translate("footer.versionInfo");
    versionInfo.setAttribute("aria-label", updateAvailable ? updateAvailableText : translate("footer.versionInfo"));
  }
  if (badge) {
    badge.classList.toggle("hidden", !updateAvailable);
    badge.setAttribute("aria-label", translate("footer.updateBadge"));
  }
  if (current) current.textContent = currentLabel;
  if (latest) latest.textContent = latestLabel;
  if (source) {
    source.textContent = payload?.source === "portable" ? translate("version.sourcePortable") : translate("version.sourceSource");
  }
  if (releaseLink) {
    releaseLink.href = payload?.release_url || "https://github.com/Brisbanehuang/ilab-gpt-conjure-poc/tree/share/v0.2.25";
  }
  if (updateButton) {
    updateButton.disabled = !(payload?.update_available && payload?.updater_available);
  }
  if (modalStatus) {
    modalStatus.textContent =
      statusText ||
      (updateAvailable
        ? formatTranslation("version.updateAvailable", { version: latestLabel })
        : payload?.updater_available === false && payload?.source !== "portable"
          ? translate("version.noUpdater")
          : translate("version.upToDate"));
  }
}

async function refreshAppVersion(): Promise<void> {
  try {
    const response = await fetch("/api/app-version");
    if (!response.ok) throw new Error(`Version API failed: ${response.status}`);
    payload = await response.json();
  } catch {
    payload = {
      current_version_label: "...",
      latest_version_label: "...",
      source: "source",
      update_available: false,
      updater_available: false,
      release_url: "https://github.com/Brisbanehuang/ilab-gpt-conjure-poc/tree/share/v0.2.25",
    };
  }
  renderAppVersion();
}

async function openUpdater(): Promise<void> {
  const updateButton = els().versionUpdateButton as HTMLButtonElement | null;
  if (updateButton) updateButton.disabled = true;
  try {
    const response = await fetch("/api/app-version/open-updater", { method: "POST" });
    if (!response.ok) throw new Error(`Updater API failed: ${response.status}`);
    payload = await response.json();
    renderAppVersion(translate("version.updaterStarted"));
  } catch {
    renderAppVersion(translate("version.updaterFailed"));
  }
}

function bindAppVersionEvents(): void {
  (els().versionInfo as HTMLElement | null)?.addEventListener("click", () => {
    renderAppVersion();
    setModalHidden(false);
  });
  (els().versionModalClose as HTMLElement | null)?.addEventListener("click", () => setModalHidden(true));
  (els().versionModal as HTMLElement | null)?.addEventListener("click", (event) => {
    if (event.target === els().versionModal) setModalHidden(true);
  });
  (els().versionUpdateButton as HTMLElement | null)?.addEventListener("click", () => {
    void openUpdater();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setModalHidden(true);
  });
  document.addEventListener(LOCALE_CHANGE_EVENT, () => renderAppVersion());
}

export function initAppVersionFeature(): void {
  if (appVersionInitialized) return;
  appVersionInitialized = true;
  bindAppVersionEvents();
  renderAppVersion();
  void refreshAppVersion();
}
