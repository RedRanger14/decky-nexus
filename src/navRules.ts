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
