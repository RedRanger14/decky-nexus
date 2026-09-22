// Updating the plugin itself, from Gaming Mode. Run via:
// pnpm run test:pluginupdate
import assert from "node:assert/strict";
import test from "node:test";

import {
  DECKY_INSTALL_TYPE_UPDATE,
  UPDATE_WAIT_MS,
  canSelfUpdate,
  formatReleaseNotes,
  isNewerVersion,
  pluginUpdateBlocked,
  pluginUpdateBody,
  pluginUpdateLabel,
  requestPluginUpdate,
  trustedArtifact,
  updateLanded,
  versionParts,
} from "../.test-build/pluginUpdate.js";

const REAL = "https://github.com/RedRanger14/decky-nexus/releases/download/v1.11.2/Nexus-Mods-1.11.2.zip";

function withRouter(fn, impl) {
  const prev = globalThis.window;
  globalThis.window = { DeckyBackend: impl };
  try {
    return fn();
  } finally {
    globalThis.window = prev;
  }
}

// --- version comparison --------------------------------------------------

test("a newer version is offered and an older one is not", () => {
  assert.equal(isNewerVersion("1.11.1", "1.11.2"), true);
  assert.equal(isNewerVersion("1.11.2", "1.11.2"), false, "equal is not an update");
  assert.equal(isNewerVersion("1.11.2", "1.11.1"), false, "never a downgrade");
});

test("1.9.9 is older than 1.11.0, whatever a string compare thinks", () => {
  // This is the whole reason the comparison is numeric. A string compare
  // puts "1.9.9" above "1.11.0" and would have offered a downgrade on the
  // day this shipped, since the plugin went 1.9.8 to 1.10.0 to 1.11.x.
  assert.equal(isNewerVersion("1.9.9", "1.11.0"), true);
  assert.equal(isNewerVersion("1.11.0", "1.9.9"), false);
  assert.equal(isNewerVersion("1.9.8", "1.10.0"), true);
});

test("a dev build ahead of the store is left alone", () => {
  // The test device usually runs a version that is not published yet.
  // Nagging it to install an older build would be worse than silence.
  assert.equal(isNewerVersion("1.12.0", "1.11.2"), false);
});

test("version parsing never throws on rubbish", () => {
  assert.deepEqual(versionParts("1.11.2"), [1, 11, 2]);
  assert.deepEqual(versionParts("v1.11.2"), [1, 11, 2]);
  assert.deepEqual(versionParts("1.11.2-beta"), [1, 11, 2]);
  for (const bad of ["", "   ", "abc", "..", undefined, null]) {
    assert.doesNotThrow(() => versionParts(bad));
    assert.equal(isNewerVersion("1.0.0", bad), false, String(bad));
  }
});

test("a missing latest version is not an update", () => {
  assert.equal(isNewerVersion("1.0.0", ""), false);
});

// --- what may be installed ----------------------------------------------

test("only this project's own release assets are trusted", () => {
  // Decky installs whatever URL it is handed, so this is the last check
  // on our side before something else's code runs as this plugin.
  assert.equal(trustedArtifact(REAL), true);
  for (const bad of [
    undefined,
    "",
    "http://github.com/RedRanger14/decky-nexus/releases/download/v1/x.zip",
    "https://example.com/Nexus-Mods-1.11.2.zip",
    "https://github.com/someoneelse/decky-nexus/releases/download/v1/x.zip",
    "https://github.com/RedRanger14/decky-nexus/archive/main.zip",
    "https://evil.invalid/#https://github.com/RedRanger14/decky-nexus/releases/download/",
  ]) {
    assert.equal(trustedArtifact(bad), false, String(bad));
  }
});

// --- the guard rails -----------------------------------------------------

const GOOD = {
  ok: true,
  current: "1.11.1",
  update_available: true,
  version: "1.11.2",
  artifact: REAL,
  hash: "43fbfc90e30536422ac6aabf3b71706d9e0a105eb5d4cdc1a1e9987bb9716b37",
};

test("nothing is said when there is no update", () => {
  const none = { ok: true, current: "1.11.2", update_available: false };
  assert.equal(pluginUpdateLabel(none), "");
  assert.equal(pluginUpdateBody(none), "");
  assert.equal(pluginUpdateBlocked(none), "");
  assert.equal(pluginUpdateLabel(undefined), "");
});

test("an untrusted or unverifiable artifact is refused, not offered", () => {
  withRouter(() => {
    assert.match(
      pluginUpdateBlocked({ ...GOOD, artifact: "https://example.com/x.zip" }),
      /could not be verified/i
    );
    assert.match(
      pluginUpdateBlocked({ ...GOOD, hash: "" }),
      /could not be verified/i
    );
    assert.equal(pluginUpdateBlocked(GOOD), "", "a good update is not blocked");
  }, { call: async () => undefined });
});

test("a Decky without the installer says so instead of doing nothing", () => {
  const prev = globalThis.window;
  globalThis.window = {};
  try {
    assert.equal(canSelfUpdate(), false);
    const why = pluginUpdateBlocked(GOOD);
    assert.match(why, /Desktop Mode/);
    // It must point somewhere, not just fail.
    assert.match(why, /README/);
  } finally {
    globalThis.window = prev;
  }
});

test("the copy says what will happen and what will not", () => {
  assert.match(pluginUpdateLabel(GOOD), /1\.11\.2/);
  const body = pluginUpdateBody(GOOD);
  assert.match(body, /1\.11\.1/, "it says which version you are on");
  assert.match(body, /confirm/i, "Decky asks before installing");
  assert.match(body, /mods, API key and settings are not/i);
  assert.doesNotMatch(body, /\u2014/, "no em dashes");
});

// --- the call itself -----------------------------------------------------

test("the loader is asked with the route and arguments it documents", async () => {
  let seen;
  const err = await withRouter(
    () => requestPluginUpdate(GOOD),
    { call: async (...args) => { seen = args; } }
  );
  assert.equal(await err, "");
  assert.equal(seen[0], "utilities/install_plugin");
  assert.equal(seen[1], GOOD.artifact);
  // The name must match the INSTALLED folder or Decky puts a second copy
  // beside this one instead of replacing it.
  assert.equal(seen[2], "Nexus Mods");
  assert.equal(seen[3], "1.11.2");
  assert.equal(seen[4], GOOD.hash);
  // 2 is UPDATE in the loader's PluginInstallType, which is what makes
  // its prompt say update rather than install.
  assert.equal(seen[5], DECKY_INSTALL_TYPE_UPDATE);
  assert.equal(DECKY_INSTALL_TYPE_UPDATE, 2);
});

test("a blocked update never reaches the loader", async () => {
  let called = false;
  const err = await withRouter(
    () => requestPluginUpdate({ ...GOOD, artifact: "https://example.com/x.zip" }),
    { call: async () => { called = true; } }
  );
  assert.equal(called, false, "an untrusted artifact was sent to Decky");
  assert.match(await err, /could not be verified/i);
});

test("a refusal from Decky is reported rather than swallowed", async () => {
  const err = await withRouter(
    () => requestPluginUpdate(GOOD),
    { call: async () => { throw new Error("boom"); } }
  );
  assert.match(await err, /Decky refused/);
  assert.match(await err, /boom/);
});

// --- recovering from a prompt that was cancelled, or an update that landed ---
// Michael updated to 1.11.4 successfully and the page sat on "Waiting for
// Decky" until he closed it. Cancelling Decky's prompt would have been the
// same dead end, because nothing tells the page either happened.

test("a wait that is long enough to be useful and short enough to recover", () => {
  assert.ok(UPDATE_WAIT_MS >= 10_000, "too short to survive a slow prompt");
  assert.ok(UPDATE_WAIT_MS <= 60_000, "a cancel must not strand the button");
});

test("the update counts as landed once the running version catches up", () => {
  assert.equal(updateLanded("1.11.4", "1.11.4"), true);
  // Ahead is landed too: a dev build installed over the top still means
  // there is nothing left to do.
  assert.equal(updateLanded("1.11.4", "1.11.5"), true);
  assert.equal(updateLanded("1.11.4", "1.11.3"), false, "still on the old build");
});

test("landing is never claimed without both versions", () => {
  assert.equal(updateLanded("1.11.4", undefined), false);
  assert.equal(updateLanded("1.11.4", ""), false);
  assert.equal(updateLanded("", "1.11.4"), false);
});

// --- release notes ------------------------------------------------------
// "it would also be great if we could put what is in the update somewhere
// as part of it" - so the notes travel with the update, not just the
// version number.

const REAL_NOTES = [
  "An unofficial, community-built Decky Loader plugin.",
  "",
  "## The plugin now updates itself",
  "",
  "Open the Quick Access Menu, press **Updates**, and a plugin update",
  "appears. No `Desktop Mode`, no terminal.",
  "",
  "## Also in this release",
  "",
  "- Beginnings of NieR:Automata, requested in [issue #25](https://x/25).",
  "- The log now names every Steam library it searched.",
  "",
  "## Installing",
  "",
  "Download `Nexus-Mods-1.11.4.zip` below.",
  "",
  "---",
  "",
  "Not affiliated with Nexus Mods or Valve.",
].join("\n");

test("nothing is shown when there are no notes", () => {
  assert.deepEqual(formatReleaseNotes(undefined), []);
  assert.deepEqual(formatReleaseNotes(""), []);
});

test("headings survive and the noise sections do not", () => {
  const lines = formatReleaseNotes(REAL_NOTES);
  const joined = lines.join("\n");
  assert.ok(lines.includes("THE PLUGIN NOW UPDATES ITSELF"));
  assert.ok(lines.includes("ALSO IN THIS RELEASE"));
  // Already installing it, so these two say nothing useful here.
  assert.doesNotMatch(joined, /INSTALLING/);
  assert.doesNotMatch(joined, /Nexus-Mods-1\.11\.4\.zip/);
  // The footer rule ends the useful part.
  assert.doesNotMatch(joined, /Not affiliated/);
});

test("markdown marks are stripped rather than shown raw", () => {
  const joined = formatReleaseNotes(REAL_NOTES).join("\n");
  assert.doesNotMatch(joined, /\*\*/, "bold markers left in");
  assert.doesNotMatch(joined, /`/, "code ticks left in");
  assert.doesNotMatch(joined, /\]\(http/, "raw link syntax left in");
  // The link's text is what a reader needs, not its URL.
  assert.match(joined, /issue #25/);
  assert.doesNotMatch(joined, /https:\/\/x\/25/);
  // Bullets stay recognisable as bullets.
  assert.match(joined, /- Beginnings of NieR:Automata/);
});

test("the notes are bounded, because this is a panel not a browser", () => {
  const huge = Array.from({ length: 500 }, (_, i) => `line ${i}`).join("\n");
  assert.ok(formatReleaseNotes(huge).length <= 40);
  assert.equal(formatReleaseNotes(huge, 5).length, 5);
});

test("blank lines are kept as separators but never doubled or trailing", () => {
  const lines = formatReleaseNotes(REAL_NOTES);
  assert.notEqual(lines[lines.length - 1], "", "trailing blank left behind");
  for (let i = 1; i < lines.length; i++) {
    assert.ok(
      !(lines[i] === "" && lines[i - 1] === ""),
      `two blank lines in a row at ${i}`
    );
  }
});

test("carriage returns from a GitHub body do not survive", () => {
  const crlf = "## Heading\r\n\r\n- one\r\n- two\r\n";
  const lines = formatReleaseNotes(crlf);
  for (const l of lines) assert.doesNotMatch(l, /\r/, JSON.stringify(l));
  assert.ok(lines.includes("HEADING"));
  assert.ok(lines.includes("- one"));
});

test("the standing preamble is dropped, so changes are what you see first", () => {
  // Every release body opens with the same paragraph about what the
  // plugin is and that it needs Premium. Both true, both worthless to
  // somebody who already has it installed, and together they filled the
  // whole collapsed view and pushed the actual changes behind "Show more".
  const lines = formatReleaseNotes(REAL_NOTES);
  const joined = lines.join("\n");
  assert.doesNotMatch(joined, /unofficial, community-built/);
  assert.doesNotMatch(joined, /Premium/);
  // The first thing shown is the first real heading.
  assert.equal(lines[0], "THE PLUGIN NOW UPDATES ITSELF");
});

test("notes with no headings at all are still shown rather than swallowed", () => {
  // A release written as a plain paragraph must not come back empty:
  // skipping the preamble only makes sense when there is a heading to
  // skip TO.
  const plain = "Fixed a crash when opening the panel.\nAlso tidied the log.";
  const lines = formatReleaseNotes(plain);
  assert.ok(lines.length >= 1, "plain notes were dropped entirely");
  assert.match(lines.join("\n"), /Fixed a crash/);
});
