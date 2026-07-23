// Back-button routing table tests. Run via: npm run test:nav
// (compiles src/navRules.ts standalone, then executes this file)
import assert from "node:assert/strict";
import test from "node:test";

import { backAction } from "../.test-build/navRules.js";

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
