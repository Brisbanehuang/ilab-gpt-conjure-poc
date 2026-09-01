import { DEFAULT_LOCALE, DICTIONARIES, LOCALES } from "./i18n/dictionaries";
import type { Locale, TranslationValues } from "./i18n/types";
import { getLegacyBridge } from "./state";

export type { Locale, TranslationValues } from "./i18n/types";

const LOCALE_STORAGE_KEY = "codex-image-locale-preference";
export const LOCALE_CHANGE_EVENT = "codex-image-locale-change";

let currentLocale: Locale = DEFAULT_LOCALE;
let i18nInitialized = false;

function canUseLocale(value: unknown): value is Locale {
  return LOCALES.includes(value as Locale);
}

export function normalizeLocale(value: unknown): Locale {
  if (canUseLocale(value)) return value;
  if (typeof value !== "string") return DEFAULT_LOCALE;
  return localeFromLanguageTag(value) || DEFAULT_LOCALE;
}

export function localeFromLanguageTag(value: unknown): Locale | null {
  if (typeof value !== "string") return null;
  const language = value.trim().toLowerCase();
  if (!language) return null;
  const exact = LOCALES.find((locale) => locale.toLowerCase() === language);
  if (exact) return exact;
  if (language.startsWith("zh-hk") || language.startsWith("zh-mo")) return "zh-HK";
  if (language.startsWith("zh-tw") || language.startsWith("zh-hant")) return "zh-TW";
  if (language.startsWith("zh-cn") || language.startsWith("zh-sg") || language.startsWith("zh-hans") || language === "zh") {
    return "zh-CN";
  }
  if (language.startsWith("ja")) return "ja";
  if (language.startsWith("ko")) return "ko";
  if (language.startsWith("en")) return "en";
  if (language.startsWith("es")) return "es";
  if (language.startsWith("pt")) return "pt";
  if (language.startsWith("fr")) return "fr";
  if (language.startsWith("de")) return "de";
  if (language.startsWith("ru")) return "ru";
  if (language.startsWith("it")) return "it";
  if (language.startsWith("hi")) return "hi";
  return null;
}

export function detectPreferredLocale(languages?: readonly string[]): Locale {
  const candidates = languages && languages.length
    ? languages
    : [
      ...Array.from(navigator.languages || []),
      navigator.language
    ];
  for (const language of candidates) {
    const locale = localeFromLanguageTag(language);
    if (locale) return locale;
  }
  return DEFAULT_LOCALE;
}

export function translate(key: string, locale: Locale = currentLocale): string {
  return DICTIONARIES[locale]?.[key] ?? DICTIONARIES[DEFAULT_LOCALE][key] ?? key;
}

export function formatTranslation(key: string, values: TranslationValues = {}, locale: Locale = currentLocale): string {
  return translate(key, locale).replace(/\{(\w+)\}/g, (match, name) => {
    const value = values[name];
    return value === undefined ? match : String(value);
  });
}

function translationPairs(value: string | undefined): Array<[string, string]> {
  const pairs: Array<[string, string]> = [];
  (value || "")
    .split(";")
    .map((item) => item.trim())
    .filter(Boolean)
    .forEach((pair) => {
      const [attribute, key] = pair.split(":").map((item) => item.trim());
      if (attribute && key) pairs.push([attribute, key]);
    });
  return pairs;
}

function languageSelectElement(): HTMLSelectElement | null {
  try {
    return getLegacyBridge().els.languageSelect as HTMLSelectElement | null | undefined || null;
  } catch {
    return null;
  }
}

function updateLanguageSelect(): void {
  const select = languageSelectElement();
  if (select && select.value !== currentLocale) select.value = currentLocale;
}

function omniLanguageSwitcherElement(): HTMLElement | null {
  try {
    return getLegacyBridge().els.omniLanguageSwitcher as HTMLElement | null | undefined || null;
  } catch {
    return null;
  }
}

function updateOmniLanguageSwitcher(): void {
  const activeLocale = currentLocale.startsWith("zh-") ? "zh-CN" : currentLocale === "en" ? "en" : "";
  omniLanguageSwitcherElement()?.querySelectorAll<HTMLButtonElement>("[data-omni-locale]").forEach((button) => {
    const active = button.dataset.omniLocale === activeLocale;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

export function applyLocaleToDocument(): void {
  document.documentElement.lang = currentLocale;
  document.documentElement.dataset.locale = currentLocale;
  document.querySelectorAll<HTMLElement>("[data-i18n]").forEach((element) => {
    element.textContent = translate(element.dataset.i18n || "");
  });
  document.querySelectorAll<HTMLElement>("[data-i18n-attr]").forEach((element) => {
    translationPairs(element.dataset.i18nAttr).forEach(([attribute, key]) => {
      element.setAttribute(attribute, translate(key));
    });
  });
  updateLanguageSelect();
  updateOmniLanguageSwitcher();
}

export function setLocale(locale: Locale, options: { persist?: boolean } = {}): void {
  currentLocale = normalizeLocale(locale);
  if (options.persist !== false) {
    try {
      localStorage.setItem(LOCALE_STORAGE_KEY, currentLocale);
    } catch {
      // Browser storage may be unavailable in restricted contexts.
    }
  }
  applyLocaleToDocument();
  document.dispatchEvent(new CustomEvent(LOCALE_CHANGE_EVENT, { detail: { locale: currentLocale } }));
}

export function restoreLocalePreference(): void {
  let saved: string | null = null;
  try {
    saved = localStorage.getItem(LOCALE_STORAGE_KEY);
  } catch {
    saved = null;
  }
  setLocale(saved ? normalizeLocale(saved) : detectPreferredLocale(), { persist: false });
}

function bindLanguageSelect(): void {
  const select = languageSelectElement();
  select?.addEventListener("change", () => {
    setLocale(normalizeLocale(select.value));
  });
}

function bindOmniLanguageSwitcher(): void {
  omniLanguageSwitcherElement()?.addEventListener("click", (event) => {
    const target = event.target;
    const button = target instanceof Element
      ? target.closest<HTMLButtonElement>("[data-omni-locale]")
      : null;
    if (!button?.dataset.omniLocale) return;
    setLocale(normalizeLocale(button.dataset.omniLocale));
  });
}

export function initI18nFeature(): void {
  if (i18nInitialized) return;
  i18nInitialized = true;
  bindLanguageSelect();
  bindOmniLanguageSwitcher();
  restoreLocalePreference();
  window.__codexImageI18n = {
    applyLocaleToDocument,
    locale: () => currentLocale,
    setLocale,
    t: translate
  };
}
