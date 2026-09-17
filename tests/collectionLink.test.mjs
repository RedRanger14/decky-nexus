// Collection link parsing and the hidden-collections copy. Run via:
// pnpm run test:link
import assert from "node:assert/strict";
import test from "node:test";

import {
  ADULT_LINK_NOTE,
  ADULT_OFF_NOTE,
  LINK_HINT,
  hiddenCollectionsNote,
  parseCollectionLink,
  unsupportedLinkNote,
} from "../.test-build/collectionLink.js";

test("a full link names the game and the slug", () => {
  assert.deepEqual(
    parseCollectionLink(
      "https://next.nexusmods.com/skyrimspecialedition/collections/qdurkx"
    ),
    { domain: "skyrimspecialedition", slug: "qdurkx" }
  );
});

test("every spelling of the link is accepted", () => {
  const want = { domain: "cyberpunk2077", slug: "iszwwe" };
  for (const t of [
    "https://www.nexusmods.com/cyberpunk2077/collections/iszwwe",
    "http://nexusmods.com/cyberpunk2077/collections/iszwwe/",
    "next.nexusmods.com/cyberpunk2077/collections/iszwwe",
    "nexusmods.com/cyberpunk2077/collections/iszwwe/revisions/12",
    "https://next.nexusmods.com/cyberpunk2077/collections/iszwwe?tab=mods",
    "https://next.nexusmods.com/cyberpunk2077/collections/iszwwe#comments",
    "  https://next.nexusmods.com/cyberpunk2077/collections/ISZWWE  ",
  ]) {
    assert.deepEqual(parseCollectionLink(t), want, t);
  }
});

test("six characters on their own are a slug to try", () => {
  assert.deepEqual(parseCollectionLink("qdurkx"), { slug: "qdurkx" });
  assert.deepEqual(parseCollectionLink(" QDURKX "), { slug: "qdurkx" });
  // An ordinary word of that shape is a candidate too: the lookup decides,
  // and the normal search still runs beside it.
  assert.deepEqual(parseCollectionLink("nolvus"), { slug: "nolvus" });
});

test("anything else is just a search", () => {
  for (const t of [
    "",
    "   ",
    "gate to sovngarde",
    "keizaal",
    "qdurk",
    "qdurkx7",
    "qdur-kx",
    // A mod link is not a collection link.
    "https://www.nexusmods.com/skyrimspecialedition/mods/12345",
    // A collection URL on some other site is not ours.
    "https://evilnexusmods.com/skyrimspecialedition/collections/qdurkx",
    "https://nexusmods.com.example/skyrimspecialedition/collections/qdurkx",
  ]) {
    assert.equal(parseCollectionLink(t), null, JSON.stringify(t));
  }
});

test("the hidden note counts and says why", () => {
  assert.equal(hiddenCollectionsNote(0, false), "");
  assert.equal(hiddenCollectionsNote(-3, true), "");
  assert.equal(
    hiddenCollectionsNote(1, true),
    "1 more matches but is hidden. " + ADULT_OFF_NOTE
  );
  assert.equal(
    hiddenCollectionsNote(12, true),
    "12 more match but are hidden. " + ADULT_OFF_NOTE
  );
  assert.equal(
    hiddenCollectionsNote(3562, false),
    "3,562 collections are hidden. " + ADULT_OFF_NOTE
  );
  assert.equal(
    hiddenCollectionsNote(1, false),
    "1 collection is hidden. " + ADULT_OFF_NOTE
  );
});

test("the copy says where the setting lives and never blames the plugin", () => {
  for (const s of [
    ADULT_OFF_NOTE,
    ADULT_LINK_NOTE,
    LINK_HINT,
    unsupportedLinkNote("fallout3"),
  ]) {
    assert.doesNotMatch(s, /—/, "no em dashes: " + s);
  }
  assert.match(ADULT_OFF_NOTE, /nexusmods\.com/);
  assert.match(ADULT_OFF_NOTE, /Content blocking/);
  assert.match(ADULT_LINK_NOTE, /hidden/);
  assert.match(unsupportedLinkNote("fallout3"), /fallout3/);
  assert.match(LINK_HINT, /six characters/);
});
