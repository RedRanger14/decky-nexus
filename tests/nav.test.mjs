// Back-button routing table tests. Run via: npm run test:nav
// (compiles src/navRules.ts standalone, then executes this file)
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import { backAction, popsToExitToQam } from "../.test-build/navRules.js";

// --- every tab must actually be a working page ---------------------------
// The Health tab shipped with no tab bar, no B-to-QAM, and no route-side
// game, so it had no navigation at all and reported "Nothing installed yet"
// on a device with mods installed. Michael, fairly: "This is basic, there
// should be a nav test that covers this."
//
// Read as source rather than imported because these files pull in @decky/ui,
// which does not exist outside the Steam client.
const read = (f) => readFileSync(new URL(`../src/${f}`, import.meta.url), "utf8");
const TABS_SRC = read("Tabs.tsx");
const INDEX_SRC = read("index.tsx");

const TAB_ENTRIES = [
  ...TABS_SRC.matchAll(
    /\{\s*id:\s*"([a-z-]+)",\s*label:\s*"[^"]+",\s*route:\s*(?:"([^"]+)"|([A-Z_]+))/g
  ),
].map((m) => ({ id: m[1], route: m[2] ?? m[3] }));

// Which file each tab's page lives in. A tab whose page is not listed here
// is a tab nobody has checked, which is exactly how this bug shipped.
const TAB_PAGE_FILE = {
  store: "BrowsePage.tsx",
  downloads: "DownloadsPage.tsx",
  manager: "ManagerPage.tsx",
  updates: "UpdatesPage.tsx",
  health: "HealthCheckPage.tsx",
  settings: null, // rendered inside index.tsx, not its own page file
};

test("the tab list parsed at all", () => {
  assert.ok(TAB_ENTRIES.length >= 5, JSON.stringify(TAB_ENTRIES));
});

// Routes are registered through constants, so resolve them first.
const ROUTE_CONSTS = Object.fromEntries(
  [...`${INDEX_SRC}
${TABS_SRC}`.matchAll(
    /const\s+([A-Z_]+)\s*=\s*"(\/nexus-mods[^"]*)"/g
  )].map((m) => [m[1], m[2]])
);
const REGISTERED = [...INDEX_SRC.matchAll(/addRoute\(\s*([^,]+),/g)]
  .map((m) => m[1].trim().replace(/^"|"$/g, ""))
  .map((a) => ROUTE_CONSTS[a] ?? a);

test("every tab has a route registered with the router", () => {
  for (const { id, route } of TAB_ENTRIES) {
    const target = route.startsWith("/") ? route : ROUTE_CONSTS[route];
    assert.ok(
      REGISTERED.includes(target),
      `tab "${id}" points at ${target ?? route}, which is never passed to ` +
        `addRoute. Registered: ${REGISTERED.join(", ")}`
    );
  }
});

test("every tab page renders the tab bar, or the user is stranded", () => {
  for (const { id } of TAB_ENTRIES) {
    const file = TAB_PAGE_FILE[id];
    if (!file) continue;
    assert.ok(
      read(file).includes(`<TabBar currentId="${id}"`),
      `${file} does not render <TabBar currentId="${id}"> - opening that ` +
        `tab leaves no way to navigate anywhere else`
    );
  }
});

test("every tab page handles LB/RB and B", () => {
  for (const { id } of TAB_ENTRIES) {
    const file = TAB_PAGE_FILE[id];
    if (!file) continue;
    const src = read(file);
    assert.ok(
      src.includes(`handleTabButtons("${id}")`),
      `${file} does not wire handleTabButtons("${id}") - LB/RB do nothing`
    );
    // Either the plain handler or one that un-layers in-page first, as
    // BrowsePage does going from results back to the home rails.
    assert.ok(
      src.includes("onCancel=") && src.includes("exitTabsToQam"),
      `${file} never reaches exitTabsToQam from onCancel - B strands the user`
    );
  }
});

test("every tab page clears Steam's header", () => {
  // Without the 40px offset Steam's own search bar sits over the page title.
  for (const { id } of TAB_ENTRIES) {
    const file = TAB_PAGE_FILE[id];
    if (!file) continue;
    assert.ok(
      read(file).includes('marginTop: "40px"'),
      `${file} has no 40px top offset - Steam's search bar covers its title`
    );
  }
});

test("every tab id has a back action defined", () => {
  for (const { id } of TAB_ENTRIES) {
    assert.ok(backAction(id), `no backAction for tab "${id}"`);
  }
});

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

test("the health check returns to the QAM, because that is where it opens", () => {
  assert.equal(backAction("health"), "open-qam");
});
