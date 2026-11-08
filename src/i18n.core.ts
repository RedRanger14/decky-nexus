// Language state and lookup, with no React dependency so pure rule
// modules (panelRules.ts) can use it in node tests.
import { fr } from "./i18n.fr";

export type Locale = "en" | "fr";

const STORAGE_KEY = "nexus-mods.ui-language";

/** Languages the plugin ships UI strings for. */
export const LOCALES: { value: Locale; label: string }[] = [
  { value: "en", label: "English" },
  { value: "fr", label: "Français" },
];

const DICTIONARIES: Record<Locale, Record<string, string> | undefined> = {
  en: undefined,
  fr,
};

function detectLocale(): Locale {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "en" || stored === "fr") return stored;
  } catch {
    /* localStorage can be unavailable in a sandboxed frame */
  }
  try {
    const nav = (navigator.language || "").toLowerCase();
    if (nav.startsWith("fr")) return "fr";
  } catch {
    /* no navigator: keep English */
  }
  return "en";
}

let current: Locale = detectLocale();
const listeners = new Set<() => void>();

export function getLocale(): Locale {
  return current;
}

/** BCP-47 tag for Intl/date/number formatting. */
export function localeTag(): string {
  return current === "fr" ? "fr-FR" : "en-GB";
}

/** Persist and broadcast a language change. */
export function setLocale(locale: Locale): void {
  if (locale === current) return;
  current = locale;
  try {
    localStorage.setItem(STORAGE_KEY, locale);
  } catch {
    /* not fatal: the choice just won't survive a reload */
  }
  listeners.forEach((fn) => fn());
}

export function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/**
 * Translate an English source string.
 *
 * `vars` fills `{name}` placeholders, e.g.
 * `t("{n} mods", { n: 12 })`. Unknown strings come back untouched.
 */
export function t(text: string, vars?: Record<string, string | number>): string {
  const dict = DICTIONARIES[current];
  let out = (dict && dict[text]) || text;
  if (vars) {
    for (const [key, value] of Object.entries(vars)) {
      out = out.split(`{${key}}`).join(String(value));
    }
  }
  return out;
}

/**
 * Pick the singular or plural English source before translating. French
 * treats 0 as singular ("0 mod"), English does not ("0 mods").
 */
export function tn(
  count: number,
  one: string,
  other: string,
  vars?: Record<string, string | number>
): string {
  const frPlural = current === "fr" ? count > 1 : count !== 1;
  return t(frPlural ? other : one, { n: count, ...vars });
}

/** Numbers the way the chosen language groups and separates them. */
export function formatNumber(value: number): string {
  return value.toLocaleString(localeTag());
}

/** Ordinal for the load-order page: English "3rd", French "3e". */
export function formatOrdinal(n: number): string {
  if (current === "fr") return n === 1 ? "1er" : `${n}e`;
  const v = n % 100;
  if (v >= 11 && v <= 13) return `${n}th`;
  switch (n % 10) {
    case 1:
      return `${n}st`;
    case 2:
      return `${n}nd`;
    case 3:
      return `${n}rd`;
    default:
      return `${n}th`;
  }
}
