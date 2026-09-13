// Load Order page rules. Run via: pnpm run test:loadorder
// (compiles src/loadOrderRules.ts standalone, then executes this file)
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import {
  dependentsOf,
  kindTag,
  matchesFilter,
  moveBounds,
  moveEntry,
  orderSummary,
  ordinal,
  pluginTitle,
  positionLabel,
  shiftFor,
  sortToast,
  stepTarget,
  toggleToast,
} from "../.test-build/loadOrderRules.js";

const read = (f) =>
  readFileSync(new URL(`../src/${f}`, import.meta.url), "utf8");
const readCode = (f) =>
  read(f)
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/(^|[^:'"\\])\/\/[^\n]*/gm, "$1");

const entry = (name, over = {}) => ({
  name,
  enabled: true,
  master: /\.es[ml]$/i.test(name),
  light: false,
  needs: [],
  missing: [],
  on_disk: true,
  mod: "",
  mod_key: "",
  collection: "",
  mod_id: null,
  skipped: "",
  positioned: true,
  ...over,
});

// A small Skyrim: two masters, then plugins with a patch that needs both
// Town and Base. Masters first, as the backend hands the list over.
const LIST = [
  entry("Base.esm"),
  entry("Late.esm", { needs: ["Base.esm"] }),
  entry("Town.esp", { needs: ["Base.esm"] }),
  entry("Spare.esp"),
  entry("Other.esp"),
  entry("TownPatch.esp", { needs: ["Base.esm", "Town.esp"] }),
];
const names = (l) => l.map((e) => e.name);

test("a plugin cannot be carried above a master it needs", () => {
  const from = names(LIST).indexOf("TownPatch.esp"); // 5
  const b = moveBounds(LIST, from);
  // Town.esp is at 2 and stays put, so the highest landing is 3.
  assert.equal(b.min, 3);
  assert.equal(b.up.name, "Town.esp");
  assert.match(b.up.why, /TownPatch needs Town loaded before it/);
});

test("a plugin cannot be carried below something that needs it", () => {
  const from = names(LIST).indexOf("Town.esp"); // 2
  const b = moveBounds(LIST, from);
  // TownPatch is at 5; once Town is pulled out it sits at 4, so Town may
  // land at 4 at most (just above it).
  assert.equal(b.max, 4);
  assert.equal(b.down.name, "TownPatch.esp");
  assert.match(b.down.why, /TownPatch needs Town loaded before it/);
  // And a regular plugin never enters the masters block.
  assert.equal(b.min, 2);
  assert.match(b.up.why, /Master files always load first/);
});

test("a master stays inside the masters block", () => {
  // Two masters nothing depends on, then a plugin: either master may
  // swap with the other, neither may drop into the plugins.
  const list = [entry("Base.esm"), entry("Free.esm"), entry("Town.esp")];
  const b = moveBounds(list, 0);
  assert.equal(b.min, 0);
  assert.equal(b.max, 1);
  assert.equal(b.down.name, "Town.esp");
  assert.match(b.down.why, /Master files load before/);
  const f = moveBounds(list, 1);
  assert.deepEqual([f.min, f.max], [0, 1]);
});

test("a master cannot pass a master that needs it", () => {
  // Late.esm needs Base.esm: Base may not drop below it.
  const b = moveBounds(LIST, 0);
  assert.equal(b.max, 0);
  assert.equal(b.down.name, "Late.esm");
});

test("an unpositioned plugin does not move at all", () => {
  const list = [...LIST, entry("Off.esp", { enabled: false, positioned: false })];
  const b = moveBounds(list, 6);
  assert.deepEqual([b.min, b.max], [6, 6]);
});

test("an order that is already wrong can only be moved towards right", () => {
  // The patch sits ABOVE Town (a bad file the sorter has not fixed yet).
  const list = [
    entry("Base.esm"),
    entry("TownPatch.esp", { needs: ["Base.esm", "Town.esp"] }),
    entry("Town.esp", { needs: ["Base.esm"] }),
  ];
  const b = moveBounds(list, 1);
  assert.equal(b.min, 1, "cannot go up");
  assert.equal(b.max, 2, "may drop below Town, which fixes it");
  const t = moveBounds(list, 2);
  assert.equal(t.max, 2, "Town cannot go down");
  assert.equal(t.min, 1, "Town may rise above the patch, which fixes it");
});

test("a step stops at the wall and only reports it when nothing moved", () => {
  const from = 5; // TownPatch
  // First press up from 5: lands on 4, no wall yet.
  let r = stepTarget(LIST, from, 5, -1);
  assert.deepEqual(r, { to: 4 });
  // A ten-step jump lands on the floor (3) with no complaint: it moved.
  r = stepTarget(LIST, from, 4, -10);
  assert.equal(r.to, 3);
  assert.equal(r.wall, undefined);
  // Pressing again against the floor names the wall.
  r = stepTarget(LIST, from, 3, -1);
  assert.equal(r.to, 3);
  assert.equal(r.wall.name, "Town.esp");
});

test("the edge of the list is a wall too", () => {
  const r = stepTarget(LIST, 4, 4, +5); // Other.esp down: TownPatch below
  assert.equal(r.to, 5);
  const again = stepTarget(LIST, 4, 5, +1);
  assert.equal(again.wall.why, "Already last");
});

test("moveEntry lands the item at the index it was carried to", () => {
  assert.deepEqual(moveEntry(["a", "b", "c", "d"], 0, 2), ["b", "c", "a", "d"]);
  assert.deepEqual(moveEntry(["a", "b", "c", "d"], 3, 1), ["a", "d", "b", "c"]);
  assert.deepEqual(moveEntry(["a", "b"], 1, 1), ["a", "b"]);
  assert.deepEqual(moveEntry(["a", "b"], 5, 0), ["a", "b"], "out of range is a no-op");
});

test("moving down makes the rows in between slide up, and only them", () => {
  // Carrying row 1 over position 3: rows 2 and 3 slide up one.
  assert.deepEqual(
    [0, 1, 2, 3, 4].map((i) => shiftFor(i, 1, 3)),
    [0, 0, -1, -1, 0]
  );
  // Carrying row 3 over position 1: rows 1 and 2 slide down one.
  assert.deepEqual(
    [0, 1, 2, 3, 4].map((i) => shiftFor(i, 3, 1)),
    [0, 1, 1, 0, 0]
  );
  assert.deepEqual([0, 1, 2].map((i) => shiftFor(i, 1, 1)), [0, 0, 0]);
});

test("the landing index and the slide agree", () => {
  // Whatever moveEntry produces, the row that slid into the carried row's
  // old slot must be the one shiftFor said would move.
  const list = ["a", "b", "c", "d", "e"];
  for (let from = 0; from < 5; from++) {
    for (let to = 0; to < 5; to++) {
      const moved = moveEntry(list, from, to);
      list.forEach((n, i) => {
        if (i === from) return;
        const newIndex = moved.indexOf(n);
        assert.equal(newIndex - i, shiftFor(i, from, to), `${from}->${to} row ${i}`);
      });
      assert.equal(moved[to], list[from]);
    }
  }
});

test("dependents are found case-insensitively", () => {
  const deps = dependentsOf(LIST, "town.ESP");
  assert.deepEqual(names(deps), ["TownPatch.esp"]);
});

test("titles drop the extension and tags read the flags", () => {
  assert.equal(pluginTitle("Some Mod - Patch.esp"), "Some Mod - Patch");
  assert.equal(pluginTitle("Thing.ESL"), "Thing");
  assert.equal(pluginTitle("weird.txt"), "weird.txt");
  assert.equal(kindTag(entry("A.esm")), "ESM");
  assert.equal(kindTag(entry("A.esp")), "ESP");
  assert.equal(kindTag(entry("A.esp", { light: true })), "ESL");
});

test("the filter matches the plugin or the mod it came with", () => {
  const e = entry("JKs_Whiterun.esp", { mod: "JK's Skyrim" });
  assert.ok(matchesFilter(e, "whiterun"));
  assert.ok(matchesFilter(e, "jk's"));
  assert.ok(matchesFilter(e, "  "));
  assert.ok(!matchesFilter(e, "riften"));
});

test("positions read as ordinals", () => {
  assert.equal(ordinal(1), "1st");
  assert.equal(ordinal(2), "2nd");
  assert.equal(ordinal(3), "3rd");
  assert.equal(ordinal(11), "11th");
  assert.equal(ordinal(12), "12th");
  assert.equal(ordinal(13), "13th");
  assert.equal(ordinal(21), "21st");
  assert.equal(ordinal(112), "112th");
  assert.equal(positionLabel(33, 212), "Loads 34th of 212");
});

test("the summary explains the rule in one line", () => {
  const s = orderSummary(LIST.map((e, i) => ({ ...e, enabled: i !== 3 })));
  assert.match(s, /^6 plugins, 5 on\./);
  assert.match(s, /the lower one wins/);
  assert.match(orderSummary([entry("A.esp")]), /^1 plugin, 1 on/);
});

test("the sort toast counts what moved and says when nothing did", () => {
  const same = sortToast(["a", "b", "c"], ["a", "b", "c"]);
  assert.equal(same.title, "Already in a good order");
  const two = sortToast(["a", "b", "c"], ["a", "c", "b"]);
  assert.equal(two.title, "Moved 2 plugins");
  const on = sortToast(["a"], ["a"], 1);
  assert.match(on.body, /1 master file that other mods need was switched on/);
});

test("the toggle toast only speaks when something else changed", () => {
  const e = entry("TownPatch.esp");
  assert.equal(toggleToast(e, true, []), undefined);
  const on = toggleToast(e, true, ["Town.esp"]);
  assert.equal(on.title, "Also switched on Town");
  assert.match(on.body, /TownPatch needs it loaded first/);
  const off = toggleToast(entry("Town.esp"), false, ["TownPatch.esp", "B.esp"]);
  assert.equal(off.title, "Also switched off TownPatch, B");
  assert.match(off.body, /They need Town/);
});

// --- the page itself --------------------------------------------------------

test("the page never reorders the DOM while carrying", () => {
  // The carried row keeps its DOM position and everything slides with
  // transforms; the reorder happens once, on drop. If the list were
  // re-keyed mid-carry, Steam's focus would fall off the moving row.
  const src = readCode("LoadOrderPage.tsx");
  assert.ok(src.includes("shiftFor("), "rows slide with shiftFor");
  assert.ok(src.includes("translateY("), "the slide is a transform");
});

test("the page claims the D-pad and bumpers only while carrying", () => {
  const src = readCode("LoadOrderPage.tsx");
  assert.ok(src.includes("preventDefault()"), "claims the direction press");
  assert.ok(src.includes("stopPropagation()"), "and stops LB/RB reaching the tabs");
  // B while carrying puts the plugin back rather than leaving the page.
  assert.ok(
    /carryRef\.current\)[\s\S]{0,200}exitTabsToQam/.test(src),
    "the root onCancel checks for a carry before exiting"
  );
});

test("the page tells the footer what each button does", () => {
  const src = read("LoadOrderPage.tsx");
  for (const prop of [
    "onOKActionDescription",
    "onOptionsActionDescription",
    "onCancelActionDescription",
  ]) {
    assert.ok(src.includes(prop), `${prop} is set on the rows`);
  }
});

test("no em dashes in the page copy", () => {
  assert.ok(!read("LoadOrderPage.tsx").includes("\u2014"));
  assert.ok(!read("loadOrderRules.ts").includes("\u2014"));
});
