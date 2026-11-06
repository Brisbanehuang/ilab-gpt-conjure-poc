import { getLegacyBridge } from "./state";
import { LOCALE_CHANGE_EVENT, translate } from "./i18n";
import {
  GIF_ALLOWED_QUALITIES,
  GIF_DEFAULT_FRAME_COUNT,
  GIF_DEFAULT_FRAME_DURATION_MS,
  GIF_DEFAULT_QUALITY,
  GIF_OUTPUT_FORMAT,
  gifModelSupported,
  normalizeGifFrameCount,
  normalizeGifFrameDuration,
} from "./gif-frame-size";
import { currentImageToolModel } from "./size-presets";
import { updateCompression, updateRequestPreview } from "./output-controls";
import { updateCustomSize, updatePixelPreview, updateSizeFromPreset } from "./custom-size-controls";

const bridge = getLegacyBridge();
const els = bridge.els;

// Static-image settings that GIF mode overrides; restored when the user leaves GIF mode.
interface SavedStaticSettings {
  quality: string;
  customMode: boolean;
  customWidth: string;
  customHeight: string;
}

let gifActive = false;
let savedStaticSettings: SavedStaticSettings | null = null;
let syncing = false;
let gifOutputInitialized = false;

function gifFormatButton(): HTMLElement | null {
  return els.outputFormatGroup?.querySelector(`[data-val="${GIF_OUTPUT_FORMAT}"]`) || null;
}

function qualityButtons(): HTMLElement[] {
  const group = els.quality?.previousElementSibling;
  return group ? Array.from(group.querySelectorAll(".radio-btn")) : [];
}

function setChoiceDisabled(button: HTMLElement, disabled: boolean, title: string): void {
  button.classList.toggle("is-disabled", disabled);
  button.setAttribute("aria-disabled", disabled ? "true" : "false");
  if (disabled && title) {
    button.title = title;
  } else {
    button.removeAttribute("title");
  }
}

function setSelectValue(select: any, value: string): void {
  if (!select || select.value === value) return;
  select.value = value;
  select.dispatchEvent(new Event("change"));
}

function syncGifFormatAvailability(): boolean {
  const supported = gifModelSupported(currentImageToolModel());
  const button = gifFormatButton();
  if (button) setChoiceDisabled(button, !supported, translate("output.gifUnsupportedModel"));
  return supported;
}

function syncQualityRestriction(): void {
  qualityButtons().forEach((button) => {
    const blocked = gifActive && !GIF_ALLOWED_QUALITIES.includes(String(button.dataset.val || ""));
    setChoiceDisabled(button, blocked, translate("output.gifQualityLimited"));
  });
}

function enterGifMode(): void {
  gifActive = true;
  savedStaticSettings = {
    quality: String(els.quality?.value || ""),
    customMode: els.size?.value === "custom",
    customWidth: String(els.customWidth?.value || ""),
    customHeight: String(els.customHeight?.value || ""),
  };
  els.settingsGrid?.classList.add("gif-mode");
  els.gifSettingsField?.setAttribute("aria-hidden", "false");
  if (!GIF_ALLOWED_QUALITIES.includes(String(els.quality?.value || ""))) {
    setSelectValue(els.quality, GIF_DEFAULT_QUALITY);
  }
  if (savedStaticSettings.customMode && els.customSizeToggle) {
    // GIF frames are sized from the ratio; the custom pixel inputs come back on exit.
    els.customSizeToggle.checked = false;
    updateSizeFromPreset();
  }
}

function leaveGifMode(restore: boolean): void {
  gifActive = false;
  els.settingsGrid?.classList.remove("gif-mode");
  els.gifSettingsField?.setAttribute("aria-hidden", "true");
  const previous = savedStaticSettings;
  savedStaticSettings = null;
  if (!restore || !previous) return;
  if (previous.quality) setSelectValue(els.quality, previous.quality);
  if (previous.customMode && els.customSizeToggle) {
    els.customSizeToggle.checked = true;
    updateSizeFromPreset();
    if (els.customWidth && previous.customWidth) els.customWidth.value = previous.customWidth;
    if (els.customHeight && previous.customHeight) els.customHeight.value = previous.customHeight;
    updateCustomSize();
  }
}

export function syncGifOutputMode(): void {
  if (syncing || !els.outputFormat) return;
  syncing = true;
  try {
    const supported = syncGifFormatAvailability();
    if (!supported && els.outputFormat.value === GIF_OUTPUT_FORMAT) {
      setSelectValue(els.outputFormat, "png");
    }
    const wantGif = els.outputFormat.value === GIF_OUTPUT_FORMAT;
    if (wantGif && !gifActive) enterGifMode();
    else if (!wantGif && gifActive) leaveGifMode(true);
    if (gifActive && !GIF_ALLOWED_QUALITIES.includes(String(els.quality?.value || ""))) {
      setSelectValue(els.quality, GIF_DEFAULT_QUALITY);
    }
    syncQualityRestriction();
    updateCompression();
    updatePixelPreview(els.size?.value === "custom" ? "custom" : els.size?.value || "");
    updateRequestPreview();
  } finally {
    syncing = false;
  }
}

// Drop GIF mode without restoring the remembered static settings (history restore / form reset
// overwrite every control afterwards and then call syncGifOutputMode()).
export function discardGifOutputMode(): void {
  if (gifActive) leaveGifMode(false);
  savedStaticSettings = null;
  syncQualityRestriction();
}

export function setGifFrameControls(frameCount: unknown, frameDurationMs: unknown): void {
  if (els.gifFrameCount) els.gifFrameCount.value = String(normalizeGifFrameCount(frameCount));
  if (els.gifFrameDuration) els.gifFrameDuration.value = String(normalizeGifFrameDuration(frameDurationMs));
}

export function resetGifFrameControls(): void {
  setGifFrameControls(GIF_DEFAULT_FRAME_COUNT, GIF_DEFAULT_FRAME_DURATION_MS);
}

function handleGifFrameInput(): void {
  updatePixelPreview(els.size?.value || "");
  updateRequestPreview();
}

function handleGifFrameChange(): void {
  setGifFrameControls(els.gifFrameCount?.value, els.gifFrameDuration?.value);
  handleGifFrameInput();
}

export function initGifOutputFeature(): void {
  if (gifOutputInitialized) return;
  gifOutputInitialized = true;
  // Disabled choices stay focusable (so their hint tooltip shows) but must not change the value.
  els.settingsGrid?.addEventListener("click", (event: Event) => {
    const target = event.target as HTMLElement | null;
    if (target?.closest?.('.radio-btn[aria-disabled="true"]')) {
      event.preventDefault();
      event.stopPropagation();
    }
  }, true);
  els.outputFormat?.addEventListener("change", syncGifOutputMode);
  [els.gifFrameCount, els.gifFrameDuration].filter(Boolean).forEach((input: any) => {
    input.addEventListener("input", handleGifFrameInput);
    input.addEventListener("change", handleGifFrameChange);
  });
  document.addEventListener(LOCALE_CHANGE_EVENT, () => {
    syncGifFormatAvailability();
    syncQualityRestriction();
  });
  syncGifFormatAvailability();
  Object.assign(bridge.methods, {
    syncGifOutputMode,
    discardGifOutputMode,
    setGifFrameControls,
    resetGifFrameControls,
  });
}
