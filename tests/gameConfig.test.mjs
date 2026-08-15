// Snapshot of what every game's config RESOLVES to.
//
// Michael, after Fallout 3 broke twice in a day from changes aimed at other
// things: "With every game we add we cant afford to break what weve already
// done else we'll go in a circle."
//
// The unit tests never caught any of it, because none of it lives in a
// function - it lives in games.ts, in launch strings and step lists and tool
// arrays that are only exercised on a handheld. This freezes those values so
// that editing a shared template, or adding a game, shows up immediately as
// a diff on every OTHER game it moved. It does not judge whether a value is
// right; it insists that a change to one is deliberate.
//
// To accept intentional changes:  UPDATE_SNAPSHOT=1 node --test tests/gameConfig.test.mjs
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync, writeFileSync, existsSync } from "node:fs";

const GAMES = new URL("../src/games.ts", import.meta.url);
const SNAP = new URL("./game-config.snapshot.json", import.meta.url);
const src = readFileSync(GAMES, "utf8");

/** Recover a concatenated string-literal expression's text. */
function joinLiterals(chunk, stopAt) {
  const pieces = [];
  const re = /"((?:[^"\\]|\\.)*)"/g;
  let m;
  while ((m = re.exec(chunk))) {
    const text = m[1].replace(/\\"/g, '"').replace(/\\\\/g, "\\");
    pieces.push(text);
    if (stopAt && text.includes(stopAt)) break;
  }
  return pieces.join("");
}

function capture() {
  const starts = [];
  const re = /\n {2}(\d+): \{/g;
  let m;
  while ((m = re.exec(src))) starts.push({ appId: m[1], at: m.index });
  const games = starts.map((s, i) => {
    const body = src.slice(
      s.at,
      i + 1 < starts.length ? starts[i + 1].at : src.length
    );
    const str = (key) => {
      const mm = body.match(new RegExp(`${key}:\\s*("(?:[^"\\\\]|\\\\.)*")`));
      return mm ? JSON.parse(mm[1]) : null;
    };
    const has = (key) => new RegExp(`\\b${key}:`).test(body);
    const num = (key) => {
      const mm = body.match(new RegExp(`\\b${key}:\\s*(\\d+)`));
      return mm ? Number(mm[1]) : null;
    };
    const li = body.indexOf("launchOptionsTemplate:");
    return {
      appId: s.appId,
      displayName: str("displayName"),
      nexusDomain: str("nexusDomain"),
      installDirName: str("installDirName"),
      modsSubdir: str("modsSubdir"),
      pluginsTxtSubpath: str("pluginsTxtSubpath"),
      pluginsTxtStyle: str("pluginsTxtStyle"),
      installMode: str("installMode"),
      frameworkName: has("framework") ? str("name") : null,
      launchOptionsTemplate:
        li >= 0 ? joinLiterals(body.slice(li, li + 1200), "%command%") : null,
      prefixTools: [...body.matchAll(/nexusModId: (\d+)/g)].map((t) => t[1]),
      manualTools: (body.match(/needsDesktopMode: true/g) || []).length,
      hasLauncherBypass: has("launcherBypass"),
      hasSetupInis: has("setupInis"),
      hasLogAdapter: has("logAdapter"),
      // Per-game launch-wait tuning. Captured because it is exactly the
      // kind of single-game number that gets nudged while working on
      // another game and is never noticed: it only shows up as a toast
      // that says the wrong thing, minutes after anyone stopped watching.
      hasOwnLauncher: has("ownLauncher"),
      longWaitAtMods: num("longWaitAtMods"),
      // Every framework cleanup list for this game, primary and extras.
      // This is the one piece of per-game config that DELETES files on
      // reset - "bin" as a prefix would take the whole game with it - so
      // it is the last place a change should be able to land unnoticed.
      cleanupPrefixes: [
        ...body.matchAll(/cleanupPrefixes:\s*\[([^\]]*)\]/g),
      ].map((m) => m[1].replace(/\s+/g, " ").trim()),
    };
  });
  games.sort((a, b) => Number(a.appId) - Number(b.appId));
  return games;
}

const current = capture();

if (process.env.UPDATE_SNAPSHOT || !existsSync(SNAP)) {
  writeFileSync(SNAP, JSON.stringify(current, null, 2) + "\n");
}
const saved = JSON.parse(readFileSync(SNAP, "utf8"));

test("the capture found every game", () => {
  assert.ok(current.length >= 8, `only found ${current.length} games`);
});

test("no game's resolved config changed unintentionally", () => {
  const byId = (list) => Object.fromEntries(list.map((g) => [g.appId, g]));
  const before = byId(saved);
  const after = byId(current);
  const moved = [];
  for (const [appId, g] of Object.entries(after)) {
    const was = before[appId];
    if (!was) {
      moved.push(`${g.displayName} (${appId}) is NEW`);
      continue;
    }
    for (const key of Object.keys(g)) {
      const a = JSON.stringify(was[key]);
      const b = JSON.stringify(g[key]);
      if (a !== b) moved.push(`${g.displayName}: ${key}\n    was ${a}\n    now ${b}`);
    }
  }
  for (const appId of Object.keys(before)) {
    if (!after[appId]) moved.push(`${before[appId].displayName} was REMOVED`);
  }
  assert.equal(
    moved.length,
    0,
    "game config changed:\n  " +
      moved.join("\n  ") +
      "\n\nIf every one of those is intended, re-run with UPDATE_SNAPSHOT=1."
  );
});
