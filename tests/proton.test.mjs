// Proton picker tests. Run via: pnpm run test:proton
// (compiles src/protonPick.ts standalone, then executes this file)
import assert from "node:assert/strict";
import test from "node:test";

import { pickProton } from "../.test-build/protonPick.js";

const tool = (name) => ({ name, displayName: name });

test("picks the newest numbered Proton, which is closest to Steam's own default", () => {
  const picked = pickProton([
    tool("proton_experimental"),
    tool("proton_9"),
    tool("proton_11"),
    tool("proton_10"),
  ]);
  assert.equal(picked.name, "proton_11");
});

test("legacy decimal names never beat a modern release", () => {
  // proton_63 is 6.3 and proton_411 is 4.11 - numerically larger, older.
  const picked = pickProton([
    tool("proton_411"),
    tool("proton_63"),
    tool("proton_9"),
  ]);
  assert.equal(picked.name, "proton_9");
});

test("falls back to Experimental when only legacy builds are installed", () => {
  const picked = pickProton([tool("proton_63"), tool("proton_experimental")]);
  assert.equal(picked.name, "proton_experimental");
});

test("takes any Proton over nothing", () => {
  assert.equal(pickProton([tool("proton_hotfix")]).name, "proton_hotfix");
});

test("no Proton installed yields nothing to set", () => {
  assert.equal(pickProton([]), undefined);
  assert.equal(pickProton([{ name: "steamlinuxruntime", displayName: "SLR" }]), undefined);
});
