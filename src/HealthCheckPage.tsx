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

import { DialogButton, Focusable, ScrollPanelGroup } from "@decky/ui";
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

import { getHealthCheck } from "./api";
import { PageBackdrop, SectionHeading, StatChip } from "./chrome";
import { SupportedGame } from "./games";
import { healthVerdict } from "./panelRules";
import { installLatest } from "./install";

const WARN = "230, 180, 80";
// ScrollPanelGroup's published props do not include the ones the other
// full-screen pages already pass; same escape hatch they use.
const Scroller: any = ScrollPanelGroup;

/** The game whose setup is being checked. Set before navigating, the same
 * way the other full-screen pages receive their subject. */
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
  const game = healthGame;
  const [report, setReport] = useState<{
    checked: number;
    needs_mods: Finding[];
    needs_dlc: Finding[];
    needs_external: Finding[];
    owned_dlc: string[];
    already_fixed: { name: string; for: string }[];
  }>();
  const [busy, setBusy] = useState(false);
  const [fixing, setFixing] = useState("");

  const run = () => {
    if (!game) return;
    setBusy(true);
    getHealthCheck(
      game.nexusDomain,
      game.installDirName,
      game.modsSubdir,
      game.appId
    )
      .then((r) =>
        setReport(
          r.ok
            ? {
                checked: r.checked ?? 0,
                needs_mods: r.needs_mods ?? [],
                needs_dlc: r.needs_dlc ?? [],
                needs_external: r.needs_external ?? [],
                owned_dlc: r.owned_dlc ?? [],
                already_fixed: r.already_fixed ?? [],
              }
            : undefined
        )
      )
      .finally(() => setBusy(false));
  };

  useEffect(run, [game?.appId]);

  const verdict = healthVerdict(
    report?.checked ?? 0,
    (report?.needs_mods.length ?? 0) +
      (report?.needs_dlc.length ?? 0) +
      (report?.needs_external.length ?? 0),
    busy
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

  const missingCount = report
    ? new Set(
        report.needs_mods.flatMap((f) =>
          (f.missing ?? []).map((m) => m.mod_id).filter(Boolean)
        )
      ).size
    : 0;

  return (
    <div style={{ height: "100%", overflow: "hidden" }}>
      <PageBackdrop height={180} blur />
      <Scroller
        focusable={false}
        style={{ height: "100%", padding: "0 24px 24px" }}
      >
        <h1
          style={{
            margin: "18px 0 2px",
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

        {missingCount > 0 && (
          <>
            <SectionHeading
              title="Mods that need other mods"
              right={
                <DialogButton
                  style={{ width: "auto", minWidth: "190px" }}
                  disabled={Boolean(fixing)}
                  onClick={installAllMissing}
                >
                  {fixing
                    ? `Installing ${fixing}…`
                    : `Install the ${missingCount} missing`}
                </DialogButton>
              }
            />
            {report!.needs_mods.map((f) => (
              <FindingCard
                key={f.name}
                tone={WARN}
                icon={<FaPuzzlePiece size={16} />}
                title={f.name}
                detail={
                  <>
                    Needs{" "}
                    <b>
                      {(f.missing ?? []).map((m) => m.name).join(", ")}
                    </b>
                    , which {(f.missing ?? []).length === 1 ? "is" : "are"} not
                    installed. Without{" "}
                    {(f.missing ?? []).length === 1 ? "it" : "them"} this mod
                    may do nothing at all, and the game will not always say so.
                  </>
                }
              />
            ))}
          </>
        )}

        {(report?.needs_dlc.length ?? 0) > 0 && (
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

        {(report?.needs_external.length ?? 0) > 0 && (
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
                    Needs{" "}
                    <b>{(f.files ?? []).map((x) => x.name).join(", ")}</b>,
                    hosted somewhere we cannot download from. Get{" "}
                    {(f.files ?? []).length === 1 ? "it" : "them"} from{" "}
                    {(f.files ?? []).map((x) => x.url).join(", ")} on a
                    computer and copy{" "}
                    {(f.files ?? []).length === 1 ? "it" : "them"} across.
                  </>
                }
              />
            ))}
          </>
        )}

        {(report?.already_fixed.length ?? 0) > 0 && (
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
            Every installed mod has what it says it needs, and every DLC any
            of them asks for is present.
            <br />
            Nothing here needs your attention.
          </div>
        )}
        <div style={{ height: "40px" }} />
      </Scroller>
    </div>
  );
}
