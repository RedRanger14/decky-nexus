// Choosing a Proton build. Kept free of Steam-client imports so it can be
// compiled and tested standalone (see tests/proton.test.mjs).

export interface CompatTool {
  /** Exact string Steam writes into config.vdf */
  name: string;
  displayName: string;
}

/** Which Proton to force for a mod loader that needs one named. Newest
 * numbered release first: it's closest to what Steam already runs the
 * game with, so forcing it doesn't change how the game plays unmodded.
 * Experimental is the fallback - always present, but it moves weekly.
 *
 * The version filter matters: Valve's pre-6.0 tools are named for their
 * decimal version with the dot removed (proton_63 is 6.3, proton_411 is
 * 4.11), and sorting those numerically would pick the oldest build on the
 * device as if it were the newest. */
export function pickProton(tools: CompatTool[]): CompatTool | undefined {
  const numbered = tools
    .map((t) => ({ tool: t, match: /^proton_(\d{1,2})$/.exec(t.name) }))
    .filter(
      (x) => x.match && Number(x.match[1]) >= 7 && Number(x.match[1]) <= 30
    )
    .sort((a, b) => Number(b.match![1]) - Number(a.match![1]));
  return (
    numbered[0]?.tool ??
    tools.find((t) => t.name === "proton_experimental") ??
    tools.find((t) => t.name.startsWith("proton_"))
  );
}
