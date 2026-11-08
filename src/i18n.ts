// React-facing half of the translation layer. The language state and the
// t()/tn() lookups live in i18n.core.ts so pure rule modules can import
// them without pulling React into their node tests.
import { useEffect, useState } from "react";

import { subscribe, t } from "./i18n.core";

export * from "./i18n.core";

/**
 * Subscribe a component to language changes. Every screen that calls
 * this re-renders when the language changes in Settings.
 */
export function useT(): typeof t {
  const [, bump] = useState(0);
  useEffect(() => subscribe(() => bump((n) => n + 1)), []);
  return t;
}
