// Full-screen Health Check: everything wrong with a setup that the game
// will not tell you about until it refuses to start.
//
// Michael asked for this months ago and was talked out of it. One day of
// testing Slay the Spire 2 settled the argument - two mods silently did not
// load for want of a library, a collection's stale pinned libraries broke
// four more, mods were switched off that had fixes published on their pages,
// and a collection listed mods Nexus no longer serves. Every one of those
// was knowable, and none of it appeared anywhere a player would look.
//
// It is a diagnostics screen, which is exactly why it needs to look good:
// somebody opens this when they are already frustrated, and a wall of red
// text is the last thing that helps. So the page leads with a verdict big
// enough to read from a sofa, and only then the detail.

import {
  ButtonItem,
  DialogButton,
  Focusable,
  Navigation,
  ScrollPanelGroup,
} from "@decky/ui";
import { toaster } from "@decky/api";
import { useEffect, useState } from "react";
import {
  FaBoxOpen,
  FaCheck,
  FaCube,
  FaExternalLinkAlt,
  FaHeartbeat,
  FaPuzzlePiece,
  FaSyncAlt,
} from "react-icons/fa";

import { buildReport, getHealthCheck, getModDetails } from "./api";
import { PageBackdrop, SectionHeading, StatChip } from "./chrome";
import { SupportedGame, frameworkModIds, getActiveGame } from "./games";
import { TabBar, exitTabsToQam, handleTabButtons, pushOurPage } from "./Tabs";
import { fitReportBody, healthVerdict } from "./panelRules";
import { installLatest } from "./install";
import { LINK_CHIP_CLASS } from "./theme";
import { setDetailOrigin, setSelectedMod } from "./state";

const WARN = "230, 180, 80";
const NEXUS_ORANGE = "#da8e35";

// The design language says progress is shown by filling the control itself,
// left to right, in brand orange. There is nothing to fill here - the check
// cannot report a percentage - so this is the same idea as a sweep: one
// orange band travelling across the banner, so a slow check reads as working
// rather than stuck. On a large Fallout 3 collection it is the difference
// between waiting and reaching for the back button.
const SWEEP_CSS = `
@keyframes nexusHealthSweep {
  0%   { transform: translateX(-100%); }
  100% { transform: translateX(300%); }
}
.nexus-health-sweep {
  position: absolute;
  left: 0;
  bottom: 0;
  height: 3px;
  width: 33%;
  border-radius: 2px;
  background: linear-gradient(
    90deg,
    rgba(218,142,53,0) 0%,
    ${NEXUS_ORANGE} 50%,
    rgba(218,142,53,0) 100%
  );
  animation: nexusHealthSweep 1.15s ease-in-out infinite;
}
@media (prefers-reduced-motion: reduce) {
  .nexus-health-sweep { animation-duration: 2.4s; }
}
`;
// ScrollPanelGroup's published props do not include the ones the other
// full-screen pages already pass; same escape hatch they use.
const Scroller: any = ScrollPanelGroup;

/** The game whose setup is being checked.
 *
 * Set when the page is opened from the QAM, but NOT when the user arrives
 * with LB/RB from another tab - which is how it shipped saying "Nothing
 * installed yet" on a device with mods installed. A tab has no opener, so
 * it has to fall back to the active game like every other tab page does.
 */
let healthGame: SupportedGame | undefined;
export const setHealthGame = (g: SupportedGame) => {
  healthGame = g;
};

interface Finding {
  name: string;
  mod_id?: number;
  missing?: { name: string; mod_id?: number; notes?: string }[];
  dlc?: string[];
  files?: { name: string; url: string }[];
}

/** Open a mod's own page inside the plugin.
 *
 * Michael, after reading a finding he could do nothing with: "I think the
 * items in the health report should be clickable as a user might want to
 * read instructions on a mod". Staying in the plugin beats the browser
 * wherever we can - the page has the description, the requirements and an
 * install button, and the user never leaves Gaming Mode.
 */
async function openMod(game: SupportedGame, modId: number, name: string) {
  const result = await getModDetails(game.nexusDomain, modId);
  if (result.ok && result.mod) {
    setSelectedMod({ game, mod: result.mod });
    setDetailOrigin("browse"); // B pops back to where we came from
    pushOurPage("/nexus-mods/mod");
  } else {
    toaster.toast({ title: "Could not open mod", body: result.error ?? name });
  }
}

/** A finding you can act on. Each one is its own focus target with its own
 * ring: a card can name several mods, and packing them into one target is
 * the bug that left four of Cyberpunk's five authors unthankable. */
function LinkChip({
  label,
  icon,
  onOpen,
}: {
  label: string;
  icon?: string;
  onOpen: () => void;
}) {
  return (
    <Focusable
      className={LINK_CHIP_CLASS}
      onActivate={onOpen}
      style={{
        display: "inline-block",
        padding: "3px 12px",
        margin: "4px 6px 0 0",
        borderRadius: "999px",
        fontSize: "12px",
        maxWidth: "100%",
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
        background: "rgba(218, 142, 53, 0.15)",
        border: `1px solid ${NEXUS_ORANGE}88`,
      }}
    >
      {icon ? `${icon} ` : ""}
      {label}
    </Focusable>
  );
}

/** One problem, as a card. Cards rather than list rows because each finding
 * is a different KIND of thing with a different remedy, and rows of
 * identical text make them look interchangeable. */
function FindingCard({
  icon,
  tone,
  title,
  detail,
  action,
}: {
  icon: React.ReactNode;
  tone: string;
  title: string;
  detail: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        gap: "12px",
        alignItems: "flex-start",
        padding: "12px 14px",
        marginBottom: "8px",
        borderRadius: "6px",
        background: "rgba(255,255,255,0.045)",
        // A single accent edge in the finding's own colour. Enough to sort
        // the list at a glance without turning the page into a warning sign.
        borderLeft: `3px solid rgba(${tone}, 0.85)`,
      }}
    >
      <span
        style={{
          display: "inline-flex",
          color: `rgb(${tone})`,
          marginTop: "2px",
          flex: "0 0 auto",
        }}
      >
        {icon}
      </span>
      <div style={{ flex: "1 1 auto", minWidth: 0 }}>
        <div style={{ fontWeight: 600, fontSize: "14px" }}>{title}</div>
        <div
          style={{
            fontSize: "12.5px",
            opacity: 0.75,
            lineHeight: 1.45,
            marginTop: "3px",
          }}
        >
          {detail}
        </div>
      </div>
      {action && <div style={{ flex: "0 0 auto" }}>{action}</div>}
    </div>
  );
}

export default function HealthCheckPage() {
  const game = healthGame ?? getActiveGame(undefined);
  const [report, setReport] = useState<{
    checked: number;
    needs_mods: Finding[];
    needs_mods_info: Finding[];
    needs_dlc: Finding[];
    needs_external: Finding[];
    owned_dlc: string[];
    already_fixed: { name: string; for: string }[];
    /** Mod debug overlays we switched off in the game's own settings. */
    debug_quieted: string[];
    /** Bannerlord mods whose rejected shader cache we removed. */
    shader_caches_fixed: string[];
    load_order_moved: number;
    era_quarantined: string[];
    known_bad: { name: string; for: string; why: string; mod_id?: number }[];
    se_parked: string[];
    address_library?: { runtime: string; have: string[]; matches: boolean };
    script_extender: {
      dll: string;
      reason: string;
      outdated: boolean;
      mod: string;
      mod_id?: number;
    }[];
    script_log?: {
      ran: boolean;
      compiled: boolean;
      stale?: boolean;
      failures: {
        script: string;
        kind: string;
        symbol: string;
        count: number;
        mod: string;
      }[];
      orphans: { script: string; kind: string; symbol: string }[];
      switched_off: { name: string; script: string; why: string }[];
    };
  }>();
  const [busy, setBusy] = useState(false);
  const [fixing, setFixing] = useState("");

  const run = () => {
    if (!game) return;
    setBusy(true);
    // Frameworks are installed by Step 1, not through the mod list, so they
    // are not tracked mods. Without telling the check about them, every
    // SMAPI mod reads as missing SMAPI - 77 of them on a Stardew setup that
    // booted perfectly.
    const frameworkIds = frameworkModIds(game);
    getHealthCheck(
      game.nexusDomain,
      game.installDirName,
      game.modsSubdir,
      game.appId,
      frameworkIds,
      game.scriptExtenderLog ?? ""
    )
      .then((r) =>
        setReport(
          r.ok
            ? {
                checked: r.checked ?? 0,
                needs_mods: r.needs_mods ?? [],
                needs_mods_info: r.needs_mods_info ?? [],
                needs_dlc: r.needs_dlc ?? [],
                needs_external: r.needs_external ?? [],
                owned_dlc: r.owned_dlc ?? [],
                already_fixed: r.already_fixed ?? [],
                debug_quieted: r.debug_quieted ?? [],
                shader_caches_fixed: r.shader_caches_fixed ?? [],
                load_order_moved: r.load_order_moved ?? 0,
                era_quarantined: r.era_quarantined ?? [],
                known_bad: r.known_bad ?? [],
                script_extender: r.script_extender ?? [],
                se_parked: r.se_parked ?? [],
                address_library: r.address_library,
                script_log: r.script_log,
              }
            : undefined
        )
      )
      .finally(() => setBusy(false));
  };

  useEffect(run, [game?.appId]);

  const log = report?.script_log;
  // The compiler ran and did not finish: every script mod is off, whatever
  // else this page found.
  // Stale = the game has not run since the mods changed, so the log
  // describes a setup that no longer exists. Michael uninstalled the
  // collection that failed, installed one he knew worked, and the page
  // still reported the failure: "I booted the game to check and it booted
  // fine so the health report was stale."
  const staleLog = Boolean(log?.ran && log.stale);
  const stackDead = Boolean(log?.ran && !log.compiled && !staleLog);
  const verdict = healthVerdict(
    report?.checked ?? 0,
    (report?.needs_mods.length ?? 0) +
      (report?.needs_dlc.length ?? 0) +
      (report?.needs_external.length ?? 0),
    busy,
    stackDead
  );

  /** Install every missing required mod the check found. The whole point of
   * knowing is not having to go and get them one at a time. */
  const installAllMissing = async () => {
    if (!game || !report) return;
    const wanted = new Map<number, string>();
    for (const f of report.needs_mods) {
      for (const m of f.missing ?? []) {
        if (m.mod_id) wanted.set(m.mod_id, m.name);
      }
    }
    for (const [modId, name] of wanted) {
      setFixing(name);
      await installLatest(game, modId, name).catch(() => undefined);
    }
    setFixing("");
    run();
  };

  // While a check is running the previous findings are history, and showing
  // them under a "Checking…" banner invites acting on them.
  const shown = busy ? undefined : report;
  const missingCount = report
    ? new Set(
        report.needs_mods.flatMap((f) =>
          (f.missing ?? []).map((m) => m.mod_id).filter(Boolean)
        )
      ).size
    : 0;

  return (
    // The wrapper every tab page needs: LB/RB switching, B back to the QAM,
    // and the 40px top offset that clears Steam's own header. Shipping
    // without it left the page with no navigation at all and Steam's search
    // bar sitting over the title.
    <Focusable
      onButtonDown={handleTabButtons("health")}
      onCancel={exitTabsToQam}
      style={{ marginTop: "40px", height: "calc(100% - 40px)" }}
    >
      <PageBackdrop height={180} blur />
      <Scroller
        focusable={false}
        onButtonDown={handleTabButtons("health")}
        style={{
          height: "100%",
          overflowY: "auto",
          padding: "0 24px 110px",
          scrollPaddingBottom: "110px",
        }}
      >
        <style>{SWEEP_CSS}</style>
        <TabBar currentId="health" />
        <h1
          style={{
            margin: "12px 0 2px",
            fontSize: "26px",
            fontWeight: 700,
            letterSpacing: "0.5px",
          }}
        >
          Health check
        </h1>
        <div style={{ fontSize: "13px", opacity: 0.6, marginBottom: "14px" }}>
          {game ? game.displayName : "No game selected"}
        </div>
        {/* The verdict, sized to be read from a sofa. Somebody opens this
            screen already annoyed; the first thing they should get is an
            answer, not a table. */}
        <Focusable
          style={{
            display: "flex",
            alignItems: "center",
            gap: "16px",
            padding: "20px 22px",
            marginTop: "18px",
            borderRadius: "8px",
            // So the sweep can sit on the banner's own bottom edge.
            position: "relative",
            overflow: "hidden",
            background: `linear-gradient(135deg, rgba(${verdict.tone}, 0.16), rgba(255,255,255,0.02))`,
            border: `1px solid rgba(${verdict.tone}, 0.35)`,
          }}
        >
          <span
            style={{
              display: "inline-flex",
              color: `rgb(${verdict.tone})`,
              fontSize: "34px",
            }}
          >
            {verdict.clean ? <FaCheck /> : <FaHeartbeat />}
          </span>
          <div style={{ flex: "1 1 auto", minWidth: 0 }}>
            <div style={{ fontSize: "21px", fontWeight: 700 }}>
              {verdict.headline}
            </div>
            <div
              style={{ fontSize: "13px", opacity: 0.8, marginTop: "4px" }}
            >
              {verdict.detail}
            </div>
          </div>
          {busy && <div className="nexus-health-sweep" />}
        </Focusable>

        <div
          style={{
            display: "flex",
            gap: "8px",
            flexWrap: "wrap",
            marginTop: "12px",
          }}
        >
          <StatChip icon={<FaCube />}>
            {report?.checked ?? 0} mods checked
          </StatChip>
          {(report?.owned_dlc.length ?? 0) > 0 && (
            <StatChip icon={<FaBoxOpen />}>
              {report!.owned_dlc.length} DLC found
            </StatChip>
          )}
        </div>
        {/* Below the verdict rather than inside it: re-running is an action
            ON the report, and inside the banner it competed with the one
            thing the page exists to say. */}
        <Focusable style={{ display: "flex", marginTop: "12px" }}>
          <DialogButton
            style={{ width: "auto", minWidth: "170px" }}
            disabled={busy || Boolean(fixing)}
            onClick={run}
          >
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "8px",
              }}
            >
              <FaSyncAlt />
              {busy ? "Checking…" : "Check again"}
            </span>
          </DialogButton>
        </Focusable>

        {/* What the game itself said. This goes ABOVE the mod-page findings
            on purpose: everything below is inference from what an author
            wrote, and this is the only part that is evidence. */}
        {/* The script extender's own verdict, translated from filenames
            into mods. Michael got "po3_SpellPerkItemDistributorF4.dll:
            disabled, incompatible with the current version of the game" on
            a black screen, which names nothing he can act on. */}
        {shown?.address_library && !shown.address_library.matches && (
          <>
            <SectionHeading title="Built for a different Fallout" />
            <FindingCard
              tone="220, 110, 110"
              icon={<FaPuzzlePiece size={16} />}
              title="This collection needs an older version of the game"
              detail={
                <>
                  Its Address Library is for Fallout{" "}
                  <b>{shown.address_library.have.join(", ")}</b>, and you are
                  running <b>{shown.address_library.runtime}</b>. That library
                  is a table of addresses for one exact build, so every script
                  mod built on it fails — which is why several plugins were
                  refused and the game crashed on the way in.
                  <br />
                  <br />
                  Nothing here can fix that: the collection was built before
                  the game was updated, and it needs the older build. The mods
                  that don't use scripts are unaffected.
                </>
              }
            />
          </>
        )}

        {(shown?.script_extender.length ?? 0) > 0 && (
          <>
            <SectionHeading title="Mods the game refused to load" />
            {(shown?.se_parked.length ?? 0) > 0 && (
              <FindingCard
                tone="143, 212, 143"
                icon={<FaCheck size={14} />}
                title={`${report!.se_parked.length} set aside for you`}
                detail={
                  <>
                    <b>{report!.se_parked.join(", ")}</b> —{" "}
                    {report!.se_parked.length === 1 ? "this was" : "these were"}{" "}
                    built for a different version of the game, so the game
                    refused {report!.se_parked.length === 1 ? "it" : "them"}{" "}
                    every launch and asked you about{" "}
                    {report!.se_parked.length === 1 ? "it" : "them"} before the
                    main menu. Nothing has changed about what loads — that had
                    already been refused — you just won't be asked again.
                  </>
                }
              />
            )}
            {report!.script_extender.map((p) => (
              <FindingCard
                key={p.dll}
                tone={p.outdated ? WARN : "220, 110, 110"}
                icon={<FaPuzzlePiece size={16} />}
                title={p.mod || p.dll}
                detail={
                  <>
                    The game's script extender would not load{" "}
                    <b>{p.dll}</b> — {p.reason}.
                    {p.outdated ? (
                      <>
                        {" "}
                        That means this mod was built for a different version
                        of the game than the one you have, so only its author
                        can fix it. The rest of your mods are unaffected;
                        this one just does nothing.
                      </>
                    ) : (
                      <>
                        {" "}
                        That usually means something it depends on is missing
                        rather than the mod being broken.
                      </>
                    )}
                    {p.mod && p.mod_id && game ? (
                      <Focusable style={{ display: "flex", flexWrap: "wrap" }}>
                        <LinkChip
                          label={p.mod}
                          onOpen={() => openMod(game, p.mod_id!, p.mod)}
                        />
                      </Focusable>
                    ) : null}
                  </>
                }
              />
            ))}
          </>
        )}

        {shown && log?.ran && (
          <>
            <SectionHeading title="What the game said" />
            {staleLog && (
              <FindingCard
                tone={WARN}
                icon={<FaSyncAlt size={14} />}
                title="The game hasn't run since you changed your mods"
                detail={
                  <>
                    Everything below describes the last time it started, which
                    was before your latest change — so it may name mods you
                    have since removed. Launch the game once and come back,
                    and this section will describe what you actually have.
                  </>
                }
              />
            )}
            {log.switched_off.map((s) => (
              <FindingCard
                key={`off:${s.name}`}
                tone="143, 212, 143"
                icon={<FaCheck size={14} />}
                title={`${s.name} — switched off`}
                detail={
                  <>
                    Its script <b>{s.script}</b> would not compile, and the
                    game stops loading <b>every</b> script mod when one fails
                    — so this single mod was stopping all the others. It has
                    been switched off for you. Start the game once and the
                    rest should come back.
                  </>
                }
              />
            ))}
            {log.orphans.map((o) => (
              <FindingCard
                key={`orphan:${o.script}`}
                tone={WARN}
                icon={<FaPuzzlePiece size={16} />}
                title={o.script}
                detail={
                  <>
                    This script is failing to compile and no installed mod
                    owns it — it was left behind by an install whose record
                    was lost. It stops every other script mod loading. Use{" "}
                    <b>Reset modding</b> in Settings to clear it out.
                  </>
                }
              />
            ))}
            {log.compiled && !staleLog && log.failures.length === 0 && (
              <FindingCard
                tone="143, 212, 143"
                icon={<FaCheck size={14} />}
                title="Your script mods all compiled"
                detail={
                  <>
                    The game read every script mod you have installed and
                    accepted all of them. This is the game's own answer, not
                    a guess — which is why anything below it is worth less.
                  </>
                }
              />
            )}
          </>
        )}

        {missingCount > 0 && shown && (
          <>
            <SectionHeading
              title="Mods that need other mods"
              right={
                // Never while the script stack is dead. A broken script
                // suspends the only evidence that says whether these matter
                // at all - the collection that omitted them boots fine when
                // the game compiles - so the button would be offering to
                // install six mods a curator deliberately left out, on top
                // of a setup that is already not running. Michael caught
                // this live: "am I clicking install the 6 missing?"
                stackDead ? undefined : (
                  <DialogButton
                    style={{ width: "auto", minWidth: "190px" }}
                    disabled={Boolean(fixing)}
                    onClick={installAllMissing}
                  >
                    {fixing
                      ? `Installing ${fixing}…`
                      : `Install the ${missingCount} missing`}
                  </DialogButton>
                )
              }
            />
            {stackDead && (
              <div
                style={{
                  fontSize: "12.5px",
                  opacity: 0.7,
                  lineHeight: 1.45,
                  margin: "-2px 0 10px",
                }}
              >
                Sort the script problem above out first. Until the game can
                compile again there is no way to tell whether any of these
                actually matter — and installing them now would add mods on
                top of a setup that is not running.
              </div>
            )}
            {report!.needs_mods.map((f) => (
              <FindingCard
                key={f.name}
                tone={WARN}
                icon={<FaPuzzlePiece size={16} />}
                title={f.name}
                detail={
                  <>
                    Needs {(f.missing ?? []).length === 1 ? "this" : "these"},
                    which {(f.missing ?? []).length === 1 ? "is" : "are"} not
                    installed. Without{" "}
                    {(f.missing ?? []).length === 1 ? "it" : "them"} this mod
                    may do nothing at all, and the game will not always say so.
                    <Focusable style={{ display: "flex", flexWrap: "wrap" }}>
                      {(f.missing ?? []).map((m) =>
                        m.mod_id && game ? (
                          <LinkChip
                            key={m.mod_id}
                            label={m.name}
                            onOpen={() => openMod(game, m.mod_id!, m.name)}
                          />
                        ) : (
                          <b key={m.name}>{m.name}</b>
                        )
                      )}
                    </Focusable>
                  </>
                }
              />
            ))}
          </>
        )}

        {(shown?.needs_dlc.length ?? 0) > 0 && (
          <>
            <SectionHeading title="Mods that need game DLC" />
            {report!.needs_dlc.map((f) => (
              <FindingCard
                key={f.name}
                tone="220, 110, 110"
                icon={<FaBoxOpen size={16} />}
                title={f.name}
                detail={
                  <>
                    Needs <b>{(f.dlc ?? []).join(", ")}</b>, which we cannot
                    find installed. This is the one that stops the game
                    starting rather than just not working — the DLC has to be
                    bought and installed in Steam, and no mod can substitute
                    for it.
                  </>
                }
              />
            ))}
          </>
        )}

        {(shown?.needs_external.length ?? 0) > 0 && (
          <>
            <SectionHeading title="Files that aren't on Nexus" />
            {report!.needs_external.map((f) => (
              <FindingCard
                key={f.name}
                tone="150, 160, 220"
                icon={<FaExternalLinkAlt size={14} />}
                title={f.name}
                detail={
                  <>
                    Needs {(f.files ?? []).length === 1 ? "a file" : "files"}{" "}
                    hosted somewhere we cannot download from. Open{" "}
                    {(f.files ?? []).length === 1 ? "it" : "them"} to read the
                    page, then get the file on a computer and copy it across.
                    <Focusable style={{ display: "flex", flexWrap: "wrap" }}>
                      {(f.files ?? []).map((x) =>
                        x.url ? (
                          <LinkChip
                            key={x.url}
                            icon="🌐"
                            label={x.name}
                            onOpen={() =>
                              Navigation.NavigateToExternalWeb(x.url)
                            }
                          />
                        ) : (
                          <b key={x.name}>{x.name}</b>
                        )
                      )}
                    </Focusable>
                  </>
                }
              />
            ))}
          </>
        )}

        {(shown?.known_bad.length ?? 0) > 0 && (
          <>
            <SectionHeading title="Not recommending these" />
            {report!.known_bad.map((k, i) => (
              <FindingCard
                key={`bad:${k.name}:${i}`}
                tone="150, 160, 220"
                icon={<FaPuzzlePiece size={16} />}
                title={k.name}
                detail={
                  <>
                    <b>{k.for}</b> lists this as required, and we are not
                    suggesting it: {k.why || "it has already failed on this device"}.
                    Installing it would break more than it fixes — open it if
                    you want to judge for yourself.
                    {k.mod_id && game ? (
                      <Focusable style={{ display: "flex", flexWrap: "wrap" }}>
                        <LinkChip
                          label={k.name}
                          onOpen={() => openMod(game, k.mod_id!, k.name)}
                        />
                      </Focusable>
                    ) : null}
                  </>
                }
              />
            ))}
          </>
        )}

        {/* Reporting a problem from a handheld: the plugin writes the part
            a player cannot be expected to know - build id, what a
            collection pinned, the log tail - and GitHub's own form takes
            the part only they can write. Nothing leaves the device until
            they press submit there, with the whole body in front of them.
            Michael wanted tickets in the repo itself rather than anything
            posted on his behalf. */}
        <Focusable style={{ margin: "6px 0 10px" }}>
          <ButtonItem
            layout="below"
            onClick={async () => {
              const r = await buildReport(
                game.nexusDomain,
                game.appId
              ).catch(() => undefined);
              const body = encodeURIComponent(
                fitReportBody(r?.body ?? "")
              );
              const title = encodeURIComponent(
                `[${game.displayName}] `
              );
              Navigation.NavigateToExternalWeb(
                "https://github.com/RedRanger14/decky-nexus/issues/new" +
                  `?title=${title}&body=${body}`
              );
            }}
          >
            Report a problem
          </ButtonItem>
        </Focusable>

        {(report?.era_quarantined?.length ?? 0) > 0 && (
          <>
            <SectionHeading title="Fixed for you" />
            <FindingCard
              key="era-quarantine"
              tone="150, 160, 220"
              icon={<FaPuzzlePiece size={16} />}
              title="Switched off mods built for an older game version"
              detail={
                report!.era_quarantined.join(", ") +
                " - code mods from a collection built for an older version " +
                "of the game, which crash at launch on this one. Switched " +
                "off in the game's launcher; tick one there to try it anyway."
              }
            />
          </>
        )}

        {(report?.load_order_moved ?? 0) > 0 && (
          <>
            <SectionHeading title="Fixed for you" />
            <FindingCard
              key="load-order"
              tone="150, 160, 220"
              icon={<FaPuzzlePiece size={16} />}
              title="Corrected the mod load order"
              detail={
                `${report!.load_order_moved} module` +
                `${report!.load_order_moved === 1 ? "" : "s"} ` +
                "were loading in an order the mods themselves say is wrong, " +
                "which shows up as warnings like 'X is loaded before Y' at " +
                "launch. Reordered to what the mods declare. Nothing was " +
                "enabled or disabled."
              }
            />
          </>
        )}

        {(report?.shader_caches_fixed?.length ?? 0) > 0 && (
          <>
            <SectionHeading title="Fixed for you" />
            <FindingCard
              key="shader-cache"
              tone="150, 160, 220"
              icon={<FaPuzzlePiece size={16} />}
              title="Cleared a shader cache the game refused"
              detail={
                `${report!.shader_caches_fixed.join(", ")} shipped a shader ` +
                "cache built for an older version of the game, and the game " +
                "refuses it and closes before reaching the menu. Removed, so " +
                "the game builds its own instead. The next launch takes " +
                "longer than usual while it does that, once."
              }
            />
          </>
        )}

        {(report?.debug_quieted?.length ?? 0) > 0 && (
          <>
            <SectionHeading title="Fixed for you" />
            <FindingCard
              key="debug-quieted"
              tone="150, 160, 220"
              icon={<FaPuzzlePiece size={16} />}
              title="Turned off a mod's debug display"
              detail={
                `${report!.debug_quieted.join(", ")} — ` +
                `${report!.debug_quieted.length === 1 ? "a mod" : "mods"} ` +
                "left a debug display switched on, putting a panel of text " +
                "over the game. Turned off in the game's own settings; the " +
                "file was backed up first and the mods are untouched. Turn " +
                "it back on in the game's Mods menu if you want it."
              }
            />
          </>
        )}

        {(shown?.needs_mods_info.length ?? 0) > 0 && (
          <>
            <SectionHeading title="Left out on purpose" />
            {report!.needs_mods_info.map((f) => (
              <FindingCard
                key={`info:${f.name}`}
                tone="150, 160, 220"
                icon={<FaPuzzlePiece size={16} />}
                title={f.name}
                detail={
                  <>
                    Its page lists{" "}
                    {(f.missing ?? []).length === 1 ? "this" : "these"} as
                    required, and the collection you installed left{" "}
                    {(f.missing ?? []).length === 1 ? "it" : "them"} out. The
                    game has not complained, so this is the curator's choice
                    rather than a fault — nothing to do unless something is
                    actually missing in-game.
                    <Focusable style={{ display: "flex", flexWrap: "wrap" }}>
                      {(f.missing ?? []).map((m) =>
                        m.mod_id && game ? (
                          <LinkChip
                            key={m.mod_id}
                            label={m.name}
                            onOpen={() => openMod(game, m.mod_id!, m.name)}
                          />
                        ) : (
                          <b key={m.name}>{m.name}</b>
                        )
                      )}
                    </Focusable>
                  </>
                }
              />
            ))}
          </>
        )}

        {(shown?.already_fixed.length ?? 0) > 0 && (
          <>
            <SectionHeading title="Sorted out already" />
            {report!.already_fixed.map((d, i) => (
              <FindingCard
                key={`${d.name}:${i}`}
                tone="143, 212, 143"
                icon={<FaCheck size={14} />}
                title={d.name}
                detail={
                  <>
                    Installed for you because <b>{d.for || "a mod"}</b> needs
                    it and it was missing. This is why the check above may
                    find nothing — it was already dealt with.
                  </>
                }
              />
            ))}
          </>
        )}
        {verdict.clean && !busy && (
          <div
            style={{
              marginTop: "28px",
              padding: "18px",
              textAlign: "center",
              fontSize: "13px",
              opacity: 0.6,
              lineHeight: 1.5,
            }}
          >
            {(shown?.needs_mods_info.length ?? 0) > 0 ? (
              <>
                Nothing here needs your attention. The mods listed above are
                ones your collection chose to leave out, and the game is
                running them as the curator intended.
              </>
            ) : (
              <>
                Every installed mod has what it says it needs, and every DLC
                any of them asks for is present.
                <br />
                Nothing here needs your attention.
              </>
            )}
          </div>
        )}
      </Scroller>
    </Focusable>
  );
}
