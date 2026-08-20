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

/** Source with comments stripped, for "this must NOT appear" assertions.
 *
 * Three tests in one session failed because a COMMENT explaining why the
 * code avoids something matched a regex looking for that something. A
 * comment is documentation, not behaviour, so absence assertions read the
 * code and leave the prose alone. (Assertions about what code DOES should
 * still use read(), so a change to the real line is caught.) */
const readCode = (f) =>
  read(f)
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/(^|[^:'"\\])\/\/[^\n]*/gm, "$1");

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

// --- launch templates must survive the launch-options plugin -------------
// This device routes Steam's launch options through decky-launch-options,
// which treats ANY token containing "=" (with no "/" before it) as an
// environment variable. Fallout 3's FOSE-aware command began with
// d=$(dirname ...) and was lifted out wholesale and set as a variable named
// "d". Steam ran `bash -c --` with no script, the game started without FOSE
// and crashed, and the only trace was one line in a debug log.

const GAMES_SRC = read("games.ts");

test("no bash launch template starts with an assignment", () => {
  const bad = GAMES_SRC.match(/bash -c '[A-Za-z_][A-Za-z0-9_]*=/);
  assert.equal(
    bad,
    null,
    `a launch template opens with ${bad && bad[0]} - decky-launch-options ` +
      `reads that as an environment variable and strips the whole script ` +
      `out of the command`
  );
});

test("every launch template hands off to %command%", () => {
  // The interface declares the field too; only the ones with a value are
  // templates.
  // Split on the PROPERTY, not the word: this test used to match the
  // identifier anywhere, so mentioning it in a comment failed the suite.
  const lines = GAMES_SRC.split(/launchOptionsTemplate\s*\??:/)
    .slice(1)
    .filter((c) => !/^\s*string/.test(c));
  assert.ok(lines.length >= 2, `only ${lines.length} templates found`);
  for (const chunk of lines) {
    const upto = chunk.slice(0, 700);
    assert.ok(
      upto.includes("%command%"),
      `a launch template never reaches %command%: ${upto.slice(0, 120)}`
    );
  }
});

// --- a step must be completable -------------------------------------------
// Fallout 3's ESM Patcher is a GUI installer asking for two paths, with no
// command line at all. It was in the automatic tool list, so Step 3 reported
// "(1)" after every attempt and could never reach zero. Michael: "the first
// basic steps still dont work before installing any mods. Its not a standard
// I am willing to accept."

test("the automatic tool list excludes tools that need a person", () => {
  const src = read("index.tsx");
  assert.ok(
    src.includes("const autoTools = (game?.prefixTools ?? []).filter("),
    "autoTools is not derived from prefixTools"
  );
  assert.ok(
    src.includes("for (const tool of autoTools) {"),
    "the apply loop still iterates every prefixTool, including ones that " +
      "cannot run headless"
  );
});

test("every game with prefix tools has at least one it can run", () => {
  // Otherwise Step 3 exists purely to announce it can do nothing.
  const games = read("games.ts");
  const blocks = games.split("prefixTools:").slice(1);
  for (const b of blocks) {
    const upto = b.slice(0, 2500);
    const tools = (upto.match(/nexusModId:/g) || []).length;
    const manual = (upto.match(/needsDesktopMode: true/g) || []).length;
    assert.ok(
      tools === 0 || manual < tools,
      "a game's prefixTools are all needsDesktopMode - Step 3 would have " +
        "nothing to do"
    );
  }
});

// --- every endorse button must be reachable with a controller ------------
// Cyberpunk installs five frameworks. All five endorse rows were inside ONE
// PanelSectionRow, and Steam treats a row as a single focus target - so the
// D-pad highlighted the whole block and A always endorsed the first author.
// Four of the five were unreachable. Michael: "i can only highlight all 5
// frameworks and it just endorses the first one".

test("framework endorse rows are not packed into one focus target", () => {
  const src = read("index.tsx");
  const i = src.indexOf("allFrameworks.map(");
  assert.ok(i > 0, "the multi-framework list is gone - re-check this test");
  const block = src.slice(i, i + 500);
  assert.ok(
    block.includes("<PanelSectionRow"),
    "each framework must sit in its own PanelSectionRow, or only the " +
      "first one can be endorsed with a controller"
  );
});

test("the endorse pill has a gamepad focus style", () => {
  // Once each Cyberpunk framework got its own row they became individually
  // selectable and completely invisible - you pressed down five times
  // through nothing before the cursor reappeared. Michael: "it doesnt have
  // any hover effect now so you cant tell when they are selected".
  const theme = read("theme.ts");
  assert.ok(
    theme.includes("ENDORSE_PILL_CLASS"),
    "no dedicated class for the endorse pill"
  );
  assert.ok(
    theme.includes("${ENDORSE_PILL_CLASS}.gpfocus"),
    "the pill has no gpfocus rule, so gamepad focus is invisible"
  );
  assert.ok(
    read("EndorseButton.tsx").includes("className={ENDORSE_PILL_CLASS}"),
    "the pill does not carry the class its focus style targets"
  );
});

test("a deleted mod is skipped for good, not every session", () => {
  // Vault Boy 101 finished with 4 remaining, all of them mods that no
  // longer exist on Nexus. The skip lived in React state only, so every
  // fresh look at the collection offered them again and the count could
  // never reach zero - which is the number Michael wants it to reach.
  const page = read("CollectionPage.tsx");
  const branch = page.slice(page.indexOf("isGoneFromNexus(result.error)"));
  assert.ok(
    branch.slice(0, 1600).includes('reason: "unavailable"'),
    "a mod gone from Nexus is not persisted as a skip, so it returns as " +
      "remaining on the next load"
  );
});

// --- what is pinned to the top of the store ------------------------------
// There is no isVisible() to assert here: these are Steam's own components,
// they only render inside Gaming Mode, and jsdom does no layout - so a
// rendered test would report every element at 0x0 and pass whatever we
// shipped. What can be checked exactly is which elements sit INSIDE the
// pinned block, and that is the property that broke: v0.219.0 pinned the
// tab bar and left the search below it, still scrolling off the top.
// Michael: "the nav is there, the search bar is still being cut off. Have
// you got no test that can do (isVisible)?"
//
// Returns the source of the element that carries position: "sticky",
// matching JSX tags properly so a self-closing <div /> or an arrow function
// in an attribute cannot end the block early.
function stickyBlock(page) {
  const anchor = page.indexOf('position: "sticky"');
  assert.notEqual(anchor, -1, "nothing on the store page is sticky");
  const start = page.lastIndexOf("<div", anchor);
  let i = start;
  let depth = 0;
  while (i < page.length) {
    const open = page.indexOf("<div", i);
    const close = page.indexOf("</div>", i);
    if (open === -1 && close === -1) break;
    if (open !== -1 && (close === -1 || open < close)) {
      // Walk to this tag's own '>', skipping {...} so that => does not
      // read as the end of the tag.
      let j = open + 4;
      let braces = 0;
      for (; j < page.length; j++) {
        if (page[j] === "{") braces++;
        else if (page[j] === "}") braces--;
        else if (page[j] === ">" && braces === 0) break;
      }
      if (page[j - 1] !== "/") depth++; // self-closing opens nothing
      i = j + 1;
    } else {
      depth--;
      if (depth === 0) return page.slice(start, close + 6);
      i = close + 6;
    }
  }
  assert.fail("the sticky block on the store page is never closed");
}

test("the store pins both its nav and its search out of the scroll", () => {
  const page = read("BrowsePage.tsx");
  const block = stickyBlock(page);
  assert.ok(
    block.includes('<TabBar currentId="store" />'),
    "the store's tab bar is outside the pinned block, so a focus scroll " +
      "hides the nav"
  );
  assert.ok(
    block.includes('label="Search"'),
    "the search field is outside the pinned block - this is exactly the " +
      "half that was still cut off after the tab bar was fixed"
  );
  assert.ok(
    /background:\s*"#/.test(block.slice(0, block.indexOf(">"))),
    "a pinned block with no opaque background lets the rails ghost through it"
  );
  assert.ok(
    !block.includes("autoFocus"),
    "something inside the pinned block takes focus - Steam scrolls what it " +
      "focuses into view, and a sticky element cannot be scrolled to, so " +
      "the page would jump to the bottom instead"
  );
});

test("focus scrolling stops short of the pinned block, not under it", () => {
  // A sticky block paints over the content - the scroller does not know part
  // of its viewport is covered, so scrolling the focused hero "into view"
  // parked it underneath. Michael: "now the top part of hero mods are being
  // cut off". Steam honours CSS scroll-padding here, which is already how
  // the last row clears the SteamOS footer bar.
  const page = read("BrowsePage.tsx");
  assert.ok(
    /scrollPaddingTop:\s*`\$\{pinned\.height\}px`/.test(page),
    "nothing keeps focus scrolling clear of the pinned nav and search, so " +
      "the row Steam focuses ends up hidden behind them"
  );
  assert.ok(
    page.includes("scrollPaddingBottom"),
    "the footer clearance went with it - the last row is unreachable again"
  );
  assert.ok(
    /ref=\{pinned\.ref\}/.test(page),
    "the pinned height is not measured from the block itself, so it will " +
      "drift the moment the header changes size"
  );
});

test("a blocked install explains itself on the page, not in a toast", () => {
  // The regulation.bin refusal names the mod in the way AND says what to
  // do about it - two clauses, where a toast shows about half of one and
  // then disappears. Michael: "you forget how little information will fit
  // on a toast message - i think there needs to an orange warning info box
  // or something so the user is informed why it install blocked".
  const page = read("ModDetailPage.tsx");
  assert.ok(
    /mod_conflict \|\| result\.script_conflict/.test(page),
    "a conflict refusal is handled as a plain install failure, so its " +
      "explanation goes to a toast that truncates it"
  );
  assert.ok(
    page.includes("<WarningBox"),
    "nothing renders the refusal on the page itself"
  );
  assert.ok(
    page.includes("setBlocked(undefined)"),
    "the warning is never cleared, so it outlives the problem it describes"
  );
  const chrome = read("chrome.tsx");
  assert.ok(
    /background:\s*"rgba\(21[0-9], 1[0-9][0-9], [0-9]+, /.test(chrome),
    "the warning box is not orange - it reads as another panel of text"
  );
});

test("the store opens at its top, not scrolled past the nav", () => {
  // The hero grid takes autoFocus so the D-pad has somewhere to land -
  // without it you had to press RB twice to leave the Store. Steam scrolls
  // whatever it focuses into view, and the hero sits below the tab bar and
  // search, so opening the page shoved both off the top. Michael: "it is
  // auto scrolling down a bit and hiding the nav and search".
  const page = read("BrowsePage.tsx");
  assert.ok(
    page.includes("<ScrollHeaderIntoView />"),
    "nothing puts the store's scroll back after autoFocus"
  );
  assert.ok(
    /autoFocus=\{!typedRecently\(\)\}/.test(page),
    "the hero lost its autoFocus - the D-pad has nowhere to land, which " +
      "is the bug this replaced"
  );
});

test("a network drop pauses a collection instead of eating the queue", () => {
  // 47 mods failed on DNS in five minutes and landed on the button as
  // "still to install" with no reason attached. A network error says
  // nothing about the mod, so it must not cost the mod its place.
  const page = read("CollectionPage.tsx");
  assert.ok(
    page.includes("isNetworkError(result.error)"),
    "the run loop does not tell a network failure from a mod failure"
  );
  assert.ok(
    page.includes("networkStopped = true"),
    "the run does not stop when the connection is gone"
  );
  assert.ok(
    /setCollectionRow\(f\.fileId, "pending"\)/.test(page),
    "a network-stopped mod is not returned to the queue as pending"
  );
  assert.ok(
    page.includes("stopped - connection lost"),
    "the summary does not say the connection went"
  );
});

test("a download row can say what went wrong, not just how fast", () => {
  // Michael turned the wifi off mid-download: the retry notice was emitted
  // by the backend and never seen, because nothing between the event and
  // the row carried a message field at all.
  assert.ok(
    read("state.ts").includes("message?: string"),
    "ActiveDownload cannot hold a message"
  );
  assert.ok(
    /updateDownload\([^)]*message/s.test(read("state.ts")),
    "updateDownload drops the message"
  );
  assert.ok(
    /p\.bps,\s*p\.message/s.test(read("index.tsx")),
    "the progress listener does not forward the message"
  );
  assert.ok(
    read("DownloadsPage.tsx").includes("d.message"),
    "the download row never renders the message"
  );
});

test("going back to the QAM lands at the top of the panel", () => {
  // Michael: "when I press back to go back to the QAM, it puts me at the
  // bottom of the nexus mods menu". Scrolling alone does not hold it -
  // Steam restores focus to the button that opened the page, near the
  // bottom, and focusing it scrolls straight back down. So the reset has to
  // move focus too, and has to run AFTER the NavigateBack pops, each of
  // which can move focus itself.
  const tabs = read("Tabs.tsx");
  assert.ok(
    tabs.includes("export function scrollQamPanelToTop"),
    "no scroll-to-top on the way back to the QAM"
  );
  assert.ok(
    /\.focus\(\)/.test(tabs),
    "scrolling without moving focus is undone by Steam's focus restore"
  );
  const exit = tabs.slice(tabs.indexOf("export function exitTabsToQam"));
  assert.ok(
    exit.indexOf("NavigateBack") < exit.indexOf("scrollQamPanelToTop"),
    "the reset runs before the pops, which then move focus again"
  );
  assert.ok(
    read("index.tsx").includes("className={PANEL_TOP_CLASS}"),
    "the panel top is unmarked, so the reset cannot find it"
  );
});

test("every framework counts as installed, not just the primary one", () => {
  // Michael, on Cyberpunk mod pages: "some required mods that are installed
  // are being marked as orange (needs installing), ArchiveXL, RED4ext for
  // example". Step 1 installs five frameworks; the mod page counted only
  // game.framework, so the other four read as missing on every page that
  // required them. The health check had it right, which is exactly how the
  // two drifted - so there is now one function and no second copy.
  const games = read("games.ts");
  assert.ok(
    games.includes("export function frameworkModIds"),
    "no shared framework-id helper"
  );
  assert.ok(
    games.includes("extraFrameworks"),
    "the helper ignores extraFrameworks, which is the whole bug"
  );
  for (const file of ["ModDetailPage.tsx", "HealthCheckPage.tsx"]) {
    assert.ok(
      read(file).includes("frameworkModIds("),
      `${file} builds its own framework list instead of sharing one`
    );
  }
  assert.ok(
    !read("ModDetailPage.tsx").includes("game.framework?.aliasModIds"),
    "ModDetailPage still has its own primary-only copy of the list"
  );
});

test("health check findings are openable and show gamepad focus", () => {
  // Michael: "I think the items in the health report should be clickable as
  // a user might want to read instructions on a mod". Same class of bug as
  // the endorse pills - several chips per card, in a column - so the focus
  // ring is not optional.
  const theme = read("theme.ts");
  const page = read("HealthCheckPage.tsx");
  assert.ok(
    theme.includes("${LINK_CHIP_CLASS}.gpfocus"),
    "the finding chip has no gpfocus rule, so gamepad focus is invisible"
  );
  assert.ok(
    page.includes("className={LINK_CHIP_CLASS}"),
    "the chip does not carry the class its focus style targets"
  );
  assert.ok(
    page.includes("Navigation.NavigateToExternalWeb"),
    "off-Nexus files have nowhere to go - that was the original request"
  );
  assert.ok(
    page.includes("pushOurPage(\"/nexus-mods/mod\")"),
    "a Nexus mod should open in the plugin, not the browser"
  );
});

test("a refusal is on screen before the install button is pressed", () => {
  // Michael: "lets just put the box there before the user clicks install,
  // why show it after?" A refusal read first costs nothing; the same
  // refusal after a 5GB download costs the download.
  const page = read("ModDetailPage.tsx");
  assert.ok(
    page.includes("getInstallBlock("),
    "nothing asks whether the install would be refused until it is tried"
  );
  const load = page.indexOf("getInstallBlock(");
  const attempt = page.indexOf("const result = await installMod(");
  assert.ok(
    load < attempt,
    "the block check happens inside the install attempt, which is the " +
      "behaviour this replaced"
  );
});

test("a stale installed-mods read cannot overwrite a newer one", () => {
  // Six call sites fire refreshInstalled, several during a run, so reads
  // overlap and can land out of order. An older response landing last put
  // the pre-install picture back: after a clean 4-mod run the button read
  // "Install required (5)", and leaving and reopening the page fixed it -
  // the records were right, the newest read just lost the race.
  const page = read("CollectionPage.tsx");
  const fn = page.slice(
    page.indexOf("const refreshInstalled = () => {"),
    page.indexOf("const refreshInstalled = () => {") + 1400
  );
  assert.ok(
    /\+\+refreshSeq\.current/.test(fn),
    "refreshInstalled does not stamp its reads, so it cannot tell which " +
      "response is newest"
  );
  assert.ok(
    /if \(stale\(\)\) return;/.test(fn),
    "nothing discards a superseded read, so an old picture can overwrite " +
      "a current one"
  );
});

test("installed-mod thumbnails honour the account's adult blur", () => {
  // The browse rows and the mod page have always blurred; My Mods did not,
  // so a preference the user set once was being kept in two places out of
  // three. Michael: "the small mod thumbnails on the my mods section are
  // not respecting the adult content settings for blur".
  const page = read("ManagerPage.tsx");
  assert.ok(
    page.includes("getShowAdult()"),
    "My Mods never reads the account's adult preference"
  );
  assert.ok(
    /filter: blur \? "blur\(/.test(page),
    "the thumbnail has no blur filter, so adult art renders unblurred " +
      "however the account is set"
  );
  assert.ok(
    /adultRef\.current\.has\(mod\.mod_id/.test(page),
    "nothing tracks WHICH installed mods are adult, so the blur cannot be " +
      "applied per row"
  );
});

test("a collection skips natives built for an older patch", () => {
  // Michael: "a lot of those should be skipped as the game has been
  // updated... Especially if is a waste of a download." A single install
  // still warns and lets the user decide; in a collection nobody chose
  // this mod individually, so spending the download on a message box
  // nobody asked for is the wrong default.
  const page = read("CollectionPage.tsx");
  assert.ok(
    page.includes("result.stale_skip"),
    "the collection run has no branch for a stale native, so it lands in " +
      "the generic failure path and reads as broken"
  );
  const branch = page.slice(page.indexOf("result.stale_skip"), page.indexOf("result.stale_skip") + 900);
  assert.ok(
    /setCollectionRow\(f\.fileId, "skipped"\)/.test(branch),
    "a skipped mod is not marked skipped, so it counts as remaining forever"
  );
  assert.ok(
    /reason: "older-game"/.test(branch),
    "the skip is not recorded with a reason, so nothing can explain it later"
  );
});

test("a skipped mod never wears a tick", () => {
  // Michael: "why not have some icon for skip instead of the ticks in the
  // mod list... The toast is too fast." A tick on a mod we deliberately
  // left out is the least honest mark available, and the toast is gone by
  // the time anyone wonders why.
  const page = read("CollectionPage.tsx");
  const badge = page.slice(
    page.indexOf("const stateBadge"),
    page.indexOf("const stateBadge") + 900
  );
  assert.ok(
    /reason === "older-game"\) return "⚠/.test(badge),
    "a mod skipped for being built against an older patch has no distinct " +
      "mark, so it reads as done or as an ordinary skip"
  );
  assert.ok(
    /· skipped · built for an older patch/.test(page),
    "the row never says WHY it was skipped, leaving the toast as the only " +
      "explanation - which is the complaint"
  );
});

test("a dll loader is exempt from the older-patch rule", () => {
  // Elden Mod Loader was last updated in 2022 and the date rule skipped it,
  // taking out the one mod in the collection that works. It is not the
  // game's framework in our config - me3 is, and we ship it - so
  // frameworkModIds never covered it, and the first fix could not have
  // worked. Michael: "it skipped every mod again!"
  const games = read("games.ts");
  assert.ok(
    /loaderModIds: \[117\]/.test(games),
    "Elden Mod Loader is not declared as a loader, so the date rule skips it"
  );
  const helper = games.slice(
    games.indexOf("export function stalenessExemptModIds"),
    games.indexOf("export function frameworkModIds")
  );
  assert.ok(
    /frameworkModIds\(game\)/.test(helper) && /loaderModIds/.test(helper),
    "the exemption list must cover BOTH the framework and dll loaders"
  );
  // Both install call sites, or one path skips what the other exempts.
  for (const f of ["install.ts", "ModDetailPage.tsx"]) {
    assert.ok(
      read(f).includes("stalenessExemptModIds(game)"),
      `${f} passes the framework list only, so its installs still skip loaders`
    );
  }
});

test("the verified badge stays hidden until its counting is trustworthy", () => {
  // EldenBoobs earned VERIFIED ON DECK having skipped all 16 of its mods:
  // the run recorded one install that never landed, so later playtime
  // promoted an empty collection to verified. Michael: "ive got cold feet
  // about the badges - lets remove them for now - just hide them."
  const page = read("BrowsePage.tsx");
  assert.match(
    page,
    /const SHOW_VERIFIED_BADGES = false;/,
    "the badge flag is on again - it must stay off until 'installed' means " +
      "a mod actually landed on the device"
  );
  assert.ok(
    /SHOW_VERIFIED_BADGES && verdict && <VerifiedBadge/.test(page),
    "the badge renders without checking the flag, so hiding it does nothing"
  );
});

test("a conflict message offers the way out, not just the instruction", () => {
  // A conflict is resolved in My Mods, and telling someone to go there while
  // making them back out and navigate by hand is an instruction pretending
  // to be help. Michael: "it would be nice if we add a 'manage my mods'
  // button to the end of the message".
  const chrome = read("chrome.tsx");
  assert.ok(
    /action\?: \{ label: string; onClick: \(\) => void \}/.test(chrome),
    "WarningBox cannot carry an action, so every refusal is a dead end"
  );
  const page = read("ModDetailPage.tsx");
  assert.ok(
    /label: "Manage my mods"/.test(page) &&
      /pushOurPage\("\/nexus-mods\/manager"\)/.test(page),
    "the blocked-install box has no route to My Mods, which is where the " +
      "conflict is actually resolved"
  );
});

test("My Mods opened from a blocked install returns to that mod", () => {
  // The conflict box sends the user to My Mods to switch the other mod off.
  // B there dropped them at the QAM, so the obvious next step - press
  // install again - meant navigating back from scratch. Michael: "it should
  // go back to the mod page where I can easily click install again."
  const mod = read("ModDetailPage.tsx");
  assert.ok(
    /markManagerReturn\(\);/.test(mod),
    "nothing marks that My Mods was opened from a mod page"
  );
  const mgr = read("ManagerPage.tsx");
  assert.ok(
    /managerReturnsToMod\(\)/.test(mgr) && /popOurPage\(\)/.test(mgr),
    "My Mods still exits to the QAM however it was opened"
  );
  assert.ok(
    /clearManagerReturn\(\)/.test(mgr),
    "the flag is never cleared, so a later visit from the tab bar would " +
      "pop instead of exiting - the opposite bug"
  );
  assert.ok(
    /exitTabsToQam\(\)/.test(mgr),
    "the normal path out of My Mods is gone"
  );
});

test("an install toast cannot launch a game that is already running", () => {
  // onClick used to be attached unconditionally, so brushing a toast fired
  // a launch - and a 183-mod collection produces a lot of toasts to brush.
  // Michael, sitting on the Witcher 3 menu: "I kept getting 'game is
  // already running'". A toast saying "it will load next time" must not
  // start anything when tapped.
  const page = read("ModDetailPage.tsx");
  assert.ok(
    /const running = isGameRunning\(game\.appId\);/.test(page),
    "the toast does not know whether the game is running"
  );
  assert.ok(
    /\.\.\.\(running \? \{ onClick: \(\) => restartGame\(game\.appId\) \} : \{\}\)/.test(
      page
    ),
    "the restart action is attached regardless of state, so a stray tap " +
      "launches the game"
  );
});

test("Repair gives parked script conflicts another go", () => {
  // 30 mods were parked because two mods edited the same script and merging
  // was off. Merging is on now and 25 of them merge cleanly - but nothing
  // re-offered them, so the only route back was clearing the parked list by
  // hand over SSH. Repair is the button that means "try again".
  const page = read("CollectionPage.tsx");
  const fn = page.slice(
    page.indexOf("const repairInstallers = async () => {"),
    page.indexOf("const repairInstallers = async () => {") + 1200
  );
  assert.ok(
    /a\.reason === "conflict"/.test(fn),
    "Repair ignores mods parked for a script conflict, so a merge that " +
      "would now succeed is never attempted"
  );
  assert.ok(
    /persistAttention\(/.test(fn),
    "the parked list is cleared only in memory, so the mods come back as " +
      "skipped on the next visit"
  );
});

test("the panel says unofficial and beta where the user can see it", () => {
  // Michael works at Nexus Mods, so this is a necessity rather than
  // modesty: nothing here is an official product, and a user hitting a bug
  // must not take it to their support team. A README nobody opens does not
  // carry that. Michael: "We also need to clearly label that its
  // 'unnofifcial' and in beta ha".
  const panel = read("index.tsx");
  assert.ok(
    /v\{version\} · unofficial beta/.test(panel),
    "the QAM version badge does not say unofficial or beta"
  );
  const readme = readFileSync(new URL("../README.md", import.meta.url), "utf8");
  assert.ok(
    /Unofficial, and in beta/.test(readme) &&
      /not an\s*\n?>?\s*official Nexus Mods product/.test(readme),
    "the README does not disclaim official status up front"
  );
});

test("Bannerlord launches BLSE through the script, never a raw env var", () => {
  // MONO_PATH is what makes BLSE work under Proton, and it cannot go in the
  // launch options directly: the game path has spaces, and
  // decky-launch-options splits tokens naively, so a quoted assignment gets
  // mangled (the Fallout 3 incident, same mechanism). The backend writes a
  // script at a no-space path instead, and the template points at it.
  const games = read("games.ts");
  const start = games.indexOf("mountandblade2bannerlord");
  const nextGame = games.indexOf(
    "nexusDomain:",
    games.indexOf("nexusDomain:", start) + 1
  );
  const bl = games.slice(start, nextGame > start ? nextGame : games.length);
  assert.ok(
    /launchOptionsTemplate: "\{blse_script\} %command%"/.test(bl),
    "Bannerlord's launch template is not the backend-written script"
  );
  // On the template VALUE, not the slice: comments may (and do) explain
  // MONO_PATH. The property itself must never contain an assignment,
  // because dlo lifts any VAR= token and mangles quoted values with spaces.
  const template = bl.match(/launchOptionsTemplate: "([^"]*)"/);
  assert.ok(template, "no launch template found for Bannerlord");
  assert.ok(
    !template[1].includes("="),
    `the launch template contains an assignment (${template[1]}) - dlo ` +
      "will mangle it"
  );
  assert.ok(
    /nexusModId: 2006/.test(bl) && /Bannerlord\.Harmony/.test(bl),
    "Harmony (mod 2006) is no longer declared - BLSE dies without it, " +
      "silently, inside its own exception handler"
  );
  assert.ok(
    /Bannerlord\.BLSE\.\*/.test(bl),
    "reset no longer removes BLSE's eight unmanifested files"
  );
});
test("frameworks never take a hero slot", () => {
  // BLSE sat in the hero band on a device where it was installed and
  // running: frameworks have no install records, so installedIds cannot see
  // them. And even uninstalled, a framework is Step 1's job - a hero tile
  // saying "install BLSE" duplicates the setup flow one screen away.
  const page = read("BrowsePage.tsx");
  // The set now also carries per-game hero exclusions (desktop tools), so
  // match the construction loosely: what matters is that frameworkModIds
  // feeds it and the blend filters on it.
  assert.ok(
    /const fwIds = new Set\(\[?\s*\.{0,3}frameworkModIds\(game\)/.test(page),
    "the hero band does not know which mods are frameworks"
  );
  const blend = page.slice(
    page.indexOf("const heroBlend"),
    page.indexOf("const heroMods")
  );
  assert.ok(
    /filter\(\(m\) => !fwIds\.has\(m\.modId\)\)/.test(blend),
    "frameworks are not filtered out of the hero blend"
  );
});

test("load more trusts the backend's has_more over page fullness", () => {
  // Page fullness cannot tell "filtered short" from "no more mods": the
  // button sat at the end of a search doing nothing when pressed.
  const page = read("BrowsePage.tsx");
  assert.ok(
    /result\.has_more \?\?/.test(page),
    "paging still infers more-ness from page size alone"
  );
});

test("an expanded collection row hands focus to its buttons", () => {
  // A Focusable WITH onActivate is a leaf: the controller can never reach
  // anything inside it. The eye button in an expanded row existed and was
  // untappable - Michael: "the eye icon when you expand the mod is not
  // focusable with a controller". Closed rows activate to expand; open rows
  // release activation to their children and the title line collapses.
  const page = read("CollectionPage.tsx");
  const rows = page.match(/onActivate=\{open \? undefined : \(\) => toggleExpand\(f\)\}/g);
  assert.ok(
    rows && rows.length === 2,
    `expected both collection rows to release activation when open, found ${rows ? rows.length : 0}`
  );
  const titles = page.match(/onActivate=\{open \? \(\) => toggleExpand\(f\) : undefined\}/g);
  assert.ok(
    titles && titles.length === 2,
    "open rows have no collapse control - the title line should activate"
  );
});

test("a needed DLC is stated above the required mods", () => {
  // No amount of installing mods fixes a missing DLC: Eagle Rising crashed
  // Michael's device after its own DLLs loaded fine, because War Sails was
  // not there. So the notice sits ABOVE the required-mods list.
  const page = read("ModDetailPage.tsx");
  const dlcAt = page.indexOf('dlcNeed !== ""');
  const reqAt = page.indexOf("requirements && requirements.length > 0 && (");
  assert.ok(dlcAt > 0, "the mod page never mentions a needed DLC");
  assert.ok(
    dlcAt < reqAt,
    "the DLC notice renders below the required mods - it outranks them"
  );
  // The author's words, never a claim about what the user owns: working out
  // which DLC a Steam install includes is game-specific and fragile.
  // readCode, not read - the comment above the notice explains this very
  // rule and would match the regex itself.
  assert.ok(
    !/you do not own|not owned|missing dlc/i.test(readCode("ModDetailPage.tsx")),
    "the page claims to know which DLC the user owns, which it cannot"
  );
});

test("requirement notes are not crammed into the nowrap pill", () => {
  // The pill is whiteSpace:nowrap with an ellipsis, so an instruction put
  // in its label is clipped while the row still looks complete. That is how
  // "Disable troop overhaul" was invisible.
  const page = read("ModDetailPage.tsx");
  assert.ok(
    !/const label = external[\s\S]{0,200}req\.notes \? ` · \$\{req\.notes\}`/.test(page),
    "requirement notes are back in the pill label, where they get clipped"
  );
  assert.ok(
    /requirementSetupNotes\(requirements\)/.test(page),
    "the mod page does not render the author's setup notes"
  );
});

test("a fresh Helldivers update is announced on the panel", () => {
  // HD2 repacked its data the day before testing: every visual mod
  // installed correctly and did nothing, which reads as a plugin bug.
  // The panel says the true cause while it is fresh, and goes quiet
  // after a week.
  const page = read("index.tsx");
  const i = page.indexOf("Game updated recently");
  assert.ok(i > 0, "the panel never mentions a fresh game update");
  const block = page.slice(Math.max(0, i - 800), i + 800);
  assert.ok(
    /hd2Layout/.test(block),
    "the update note is not gated to live-service (hd2Layout) games"
  );
  assert.ok(
    /updated_days_ago <= 7/.test(block),
    "the note has no freshness cutoff, so it would show forever"
  );
});
