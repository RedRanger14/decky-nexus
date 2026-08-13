// Where the B button goes from each full-screen page - ONE table instead
// of per-page copies of the reasoning, because we kept getting it wrong:
// pages ENTERED FROM the QAM return to it (QAM-first so gamepad focus
// lands inside, then pop); pages PUSHED ON TOP of another page just pop;
// result views step back in-page first.
// Covered by tests/nav.test.mjs (npm run test:nav).

export type PageId =
  | "browse-home"
  | "browse-results"
  | "browse-collections"
  | "collection"
  | "detail-from-browse"
  | "detail-from-qam"
  | "downloads"
  | "manager"
  | "updates";

export type BackAction = "pop" | "open-qam" | "in-page";

export function backAction(page: PageId): BackAction {
  switch (page) {
    // Pushed on top of the store/downloads - B returns to where the
    // user came from, never the QAM.
    case "collection":
    case "detail-from-browse":
      return "pop";
    // Result views un-layer in-page first (back to the home rails /
    // out of collections mode), only home exits.
    case "browse-results":
    case "browse-collections":
      return "in-page";
    // Entered from QAM buttons/tabs - B returns to the QAM.
    default:
      return "open-qam";
  }
}

/** How many pages to pop when leaving our full-screen pages for the QAM.
 *
 * Exactly the depth we are at - no +1. The old exit popped depth+1, which
 * over-popped whenever a page had been reached without a counted push, and
 * under-popped when several uncounted pushes had happened. Both go wrong in
 * the same visible way: B in the QAM closes it and reveals a stale Nexus
 * page instead of the game.
 *
 * Never negative: over-popping walks into Steam's own screens, which is
 * worse than leaving one of ours behind.
 */
export function popsToExitToQam(depth: number): number {
  return Math.max(0, Math.floor(depth));
}
