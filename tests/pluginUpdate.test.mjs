// Updating the plugin itself, from Gaming Mode. Run via:
// pnpm run test:pluginupdate
import assert from "node:assert/strict";
import test from "node:test";

import {
  DECKY_INSTALL_TYPE_UPDATE,
  canSelfUpdate,
  isNewerVersion,
  pluginUpdateBlocked,
  pluginUpdateBody,
  pluginUpdateLabel,
  requestPluginUpdate,
  trustedArtifact,
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
  assert.doesNotMatch(body, /—/, "no em dashes");
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
