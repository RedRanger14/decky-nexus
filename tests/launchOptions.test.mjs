// Merging a WINEDLLOVERRIDES without taking away what was already there.
//
// The first case below is the one that happened: a device whose Shadow of
// War carried a hand-wired ReShade on d3d11, and a ReShade package install
// that replaced the whole line with its own override. The game then loaded
// a 2016 ReShade instead of the user's 4.9.1 - and, being from before
// DXVK, that one stood in for DXGI and took the game down with it.
import assert from "node:assert/strict";
import test from "node:test";
import { mergeLaunchOptions } from "../.test-build/launchOptions.js";

test("keeps overrides the template does not mention", () => {
  const got = mergeLaunchOptions(
    'WINEDLLOVERRIDES="d3dcompiler_47=n;d3d11=n,b" %command%',
    'WINEDLLOVERRIDES="dxgi=n,b" %command%'
  );
  assert.match(got, /d3dcompiler_47=n/);
  assert.match(got, /d3d11=n,b/);
  assert.match(got, /dxgi=n,b/);
  assert.match(got, /%command%/);
});

test("the template wins for a dll both name", () => {
  const got = mergeLaunchOptions(
    'WINEDLLOVERRIDES="dxgi=b" %command%',
    'WINEDLLOVERRIDES="dxgi=n,b" %command%'
  );
  assert.equal(got, 'WINEDLLOVERRIDES="dxgi=n,b" %command%');
});

test("a wrapper script survives", () => {
  // fgmod and Lossless Scaling both install themselves this way.
  const got = mergeLaunchOptions(
    "~/fgmod/fgmod %command%",
    'WINEDLLOVERRIDES="dxgi=n,b" %command%'
  );
  assert.match(got, /~\/fgmod\/fgmod/);
  assert.match(got, /WINEDLLOVERRIDES="dxgi=n,b"/);
  // The wrapper still receives the command.
  assert.ok(got.indexOf("fgmod") < got.indexOf("%command%"));
});

test("other env vars are untouched", () => {
  const got = mergeLaunchOptions(
    'DXVK_HUD=fps WINEDLLOVERRIDES="winmm=n,b" %command%',
    'WINEDLLOVERRIDES="dxgi=n,b" %command%'
  );
  assert.match(got, /DXVK_HUD=fps/);
  assert.match(got, /winmm=n,b/);
  assert.match(got, /dxgi=n,b/);
});

test("empty existing options take the template as-is", () => {
  assert.equal(
    mergeLaunchOptions("", 'WINEDLLOVERRIDES="dxgi=n,b" %command%'),
    'WINEDLLOVERRIDES="dxgi=n,b" %command%'
  );
});

test("an empty template changes nothing", () => {
  assert.equal(mergeLaunchOptions("~/fgmod/fgmod %command%", ""),
    "~/fgmod/fgmod %command%");
});

test("no %command% still yields a usable line", () => {
  const got = mergeLaunchOptions("DXVK_HUD=fps", 'WINEDLLOVERRIDES="dxgi=n,b"');
  assert.match(got, /DXVK_HUD=fps/);
  assert.match(got, /dxgi=n,b/);
});

test("a bare disabled override is preserved verbatim", () => {
  // "dinput8" with no mode means disabled, and is legal.
  const got = mergeLaunchOptions(
    'WINEDLLOVERRIDES="dinput8" %command%',
    'WINEDLLOVERRIDES="dxgi=n,b" %command%'
  );
  assert.match(got, /dinput8/);
  assert.match(got, /dxgi=n,b/);
});

test("unquoted overrides merge too", () => {
  const got = mergeLaunchOptions(
    "WINEDLLOVERRIDES=d3d11=n,b %command%",
    'WINEDLLOVERRIDES="dxgi=n,b" %command%'
  );
  assert.match(got, /d3d11=n,b/);
  assert.match(got, /dxgi=n,b/);
});

test("merging twice is stable", () => {
  const tpl = 'WINEDLLOVERRIDES="dxgi=n,b" %command%';
  const once = mergeLaunchOptions(
    'WINEDLLOVERRIDES="d3d11=n,b" %command%', tpl);
  assert.equal(mergeLaunchOptions(once, tpl), once);
});
