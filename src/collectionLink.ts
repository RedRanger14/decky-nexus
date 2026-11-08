/** Finding ONE collection from what a person can type into the search box.
 *
 * Two things hide collections from the store's own search, and both looked
 * to the reporter of #30 like the collection did not exist:
 *
 * 1. The account's adult-content gate. Measured against the live API on
 *    2026-09-17: 3,562 of Skyrim's 4,733 collections are flagged adult,
 *    including the five most endorsed (Gate To Sovngarde among them), and
 *    an account with adult content off cannot see any of them here, just
 *    as it cannot on the website. That gate follows the account and is not
 *    ours to open. What IS ours is the silence: the store now says how
 *    many it hid and why, in the words the QAM already uses.
 *
 * 2. The search matches names only. Not authors, and not slugs, so pasting
 *    the link a friend sent finds nothing. This module recognises a link,
 *    or the six characters after /collections/ in one, which is all a
 *    controller user should ever have to type.
 *
 * Pure functions, tested in tests/collectionLink.test.mjs.
 */

export interface CollectionLink {
  /** The collection's slug, lower-cased: the id in the URL. */
  slug: string;
  /** The game domain named by the link, when the text was a full link. A
   * bare slug names no game; the lookup answers with the real one. */
  domain?: string;
}

// A nexusmods.com collection link in any of its spellings: with or without
// a scheme, on www. or next., with a revision, query or fragment after the
// slug. Anchored to the start or to "//" so evilnexusmods.com is not one.
const LINK_RE =
  /(?:^|\/\/)(?:[a-z0-9-]+\.)*nexusmods\.com\/([a-z0-9_-]+)\/collections\/([a-z0-9_-]+)(?:[/?#]|$)/i;

// Slugs are six characters of [a-z0-9] on every collection seen (oxtos9,
// qdurkx, iszwwe...). Ordinary words are that shape too ("nolvus"), so a
// bare match is a candidate to LOOK UP alongside the normal search, never a
// reason to skip it.
const SLUG_RE = /^[a-z0-9]{6}$/i;

/** The collection a piece of search text names, or null when it is just a
 * search. */
export function parseCollectionLink(text: string): CollectionLink | null {
  const t = (text || "").trim();
  if (!t) return null;
  const m = LINK_RE.exec(t);
  if (m) return { domain: m[1].toLowerCase(), slug: m[2].toLowerCase() };
  if (SLUG_RE.test(t)) return { slug: t.toLowerCase() };
  return null;
}

/** The QAM's wording for the gate, kept to one sentence so it fits under a
 * heading. Where to turn it on is stated because the plugin cannot do it. */
type Translate = (text: string, vars?: Record<string, string | number>) => string;

let tr: Translate = (text, vars) => {
  if (!vars) return text;
  let out = text;
  for (const [key, value] of Object.entries(vars)) {
    out = out.split(`{${key}}`).join(String(value));
  }
  return out;
};

/** Install the UI translator. Called once by the panel entry point. */
export function setCollectionLinkTranslator(fn: Translate): void {
  tr = fn;
}

export const ADULT_OFF_NOTE =
  "Adult content is off on your Nexus Mods account. " +
  "Turn it on at nexusmods.com under Settings, Content blocking.";

/** What to say when the gate hid some of what the store fetched. `n` is
 * how many, `searching` whether these were matches for a search or the
 * whole list. Empty when nothing was hidden, so callers can render it
 * unconditionally. */
export function hiddenCollectionsNote(n: number, searching: boolean): string {
  if (!n || n <= 0) return "";
  const count = n.toLocaleString("en-GB");
  const what = searching
    ? n === 1
      ? tr("{count} more matches but is hidden.", { count })
      : tr("{count} more match but are hidden.", { count })
    : n === 1
    ? tr("{count} collection is hidden.", { count })
    : tr("{count} collections are hidden.", { count });
  return `${what} ${tr(ADULT_OFF_NOTE)}`;
}

/** A link that resolved to a collection the gate hides. Says so instead of
 * showing nothing, and instead of showing the collection: the lookup is
 * not a way round the account's setting. */
export const ADULT_LINK_NOTE =
  "That collection is marked adult, so it is hidden. " + ADULT_OFF_NOTE;

/** A link to a collection for a game this plugin does not have. */
export function unsupportedLinkNote(domain: string): string {
  return tr(
    "That is a collection for {domain}, which this plugin does not support yet.",
    { domain }
  );
}

/** Shown beside an empty collection search. The realistic controller path
 * is the six characters, not the whole link. */
export const LINK_HINT =
  "Have a link? Type the six characters after /collections/ in it.";
