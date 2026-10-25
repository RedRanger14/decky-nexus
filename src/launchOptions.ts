// Merging a WINEDLLOVERRIDES into launch options the user already has.
//
// Written after a ReShade install replaced this, on a real device:
//
//   WINEDLLOVERRIDES="d3dcompiler_47=n;d3d11=n,b" %command%
//
// with this:
//
//   WINEDLLOVERRIDES="dxgi=n,b" %command%
//
// which switched off the ReShade the user had wired up themselves and left
// a decade-old one loading in its place. Launch options are not ours: a
// game's line may carry a frame generator, a wrapper script, an env var
// somebody spent an evening on. Adding one override must not cost them the
// rest.
//
// Pure string work, no Steam globals, so it can be tested on its own.

const OVERRIDE_RE = /WINEDLLOVERRIDES\s*=\s*("([^"]*)"|'([^']*)'|([^\s]+))/;

/** The dll=mode pairs in an override string, in order, last one winning. */
function parseOverrides(value: string): Map<string, string> {
  const out = new Map<string, string>();
  for (const part of value.split(";")) {
    const entry = part.trim();
    if (!entry) continue;
    const eq = entry.indexOf("=");
    // "dxgi" alone is legal and means "disabled"; keep it verbatim.
    const name = (eq === -1 ? entry : entry.slice(0, eq)).trim().toLowerCase();
    if (!name) continue;
    out.set(name, entry);
  }
  return out;
}

function readOverrides(options: string): string {
  const m = OVERRIDE_RE.exec(options);
  if (!m) return "";
  return m[2] ?? m[3] ?? m[4] ?? "";
}

/**
 * Merge the WINEDLLOVERRIDES of `template` into `existing`, keeping
 * everything else `existing` had.
 *
 * - A dll named by both sides takes the template's mode: the caller is
 *   installing something that needs it.
 * - A dll only `existing` names is kept. This is the whole point.
 * - Anything in `existing` that is not an override - wrappers, other env
 *   vars, %command% itself - is preserved in place.
 * - An `existing` with no overrides at all gets the template's, inserted
 *   before %command% so the variable applies to the command.
 */
export function mergeLaunchOptions(existing: string, template: string): string {
  const from = (existing ?? "").trim();
  const add = (template ?? "").trim();
  if (!add) return from;
  if (!from) return add;

  const wanted = parseOverrides(readOverrides(add));
  if (wanted.size === 0) return from;

  const current = readOverrides(from);
  if (current) {
    const merged = parseOverrides(current);
    for (const [name, entry] of wanted) merged.set(name, entry);
    const value = [...merged.values()].join(";");
    return from.replace(OVERRIDE_RE, `WINEDLLOVERRIDES="${value}"`);
  }

  // No overrides yet. Put ours in front of %command% if there is one, so a
  // wrapper script keeps receiving the command as its argument.
  const value = [...wanted.values()].join(";");
  const decl = `WINEDLLOVERRIDES="${value}"`;
  const i = from.indexOf("%command%");
  if (i === -1) return `${decl} ${from}`.trim();
  return `${from.slice(0, i)}${decl} ${from.slice(i)}`.replace(/\s+/g, " ").trim();
}
