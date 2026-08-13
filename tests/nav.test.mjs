// Back-button routing table tests. Run via: npm run test:nav
// (compiles src/navRules.ts standalone, then executes this file)
import assert from "node:assert/strict";
import test from "node:test";

import { backAction, popsToExitToQam } from "../.test-build/navRules.js";

test("collection page pops back to the page beneath it (store home or downloads), never the QAM", () => {
  assert.equal(backAction("collection"), "pop");
});

test("detail page opened from browse pops back to browse", () => {
  assert.equal(backAction("detail-from-browse"), "pop");
});

test("detail page opened from the QAM eye returns to the QAM", () => {
  assert.equal(backAction("detail-from-qam"), "open-qam");
});

test("browse result views step back in-page before exiting", () => {
  assert.equal(backAction("browse-results"), "in-page");
  assert.equal(backAction("browse-collections"), "in-page");
});

test("QAM-entered pages return to the QAM", () => {
  for (const page of ["browse-home", "downloads", "manager", "updates"]) {
    assert.equal(backAction(page), "open-qam", page);
  }
});


// ---- exiting our pages for the QAM -------------------------------------
// The recurring B-in-QAM bug: press B in the QAM and it closes to reveal a
// stale Nexus page instead of the game. Cause was an exit that popped
// depth+1 while the depth itself was counted in three places and pushed
// from twenty.

test("exit pops exactly the depth, never one more", () => {
  assert.equal(popsToExitToQam(1), 1);
  assert.equal(popsToExitToQam(3), 3);
});

test("nothing open pops nothing", () => {
  assert.equal(popsToExitToQam(0), 0);
});

test("never pops past our own pages", () => {
  // Over-popping walks into Steam's screens, which is worse than leaving
  // one of ours behind.
  assert.equal(popsToExitToQam(-2), 0);
});
