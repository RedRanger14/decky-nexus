// A square thumbs-up that sits beside a framework's "installed ✓" row in
// the QAM.
//
// SMAPI, SKSE and REFramework are installed by pressing Step 1. Nobody
// opens their mod page, because there is no reason to - which means the
// mods every player of a game depends on are the ones least likely to
// ever get endorsed. This row is the only place those authors are
// reachable without asking the user to go looking for them.
//
// Rules and copy live in panelRules.endorseControl so they are testable.

import { Focusable } from "@decky/ui";
import { toaster } from "@decky/api";
import { useEffect, useState } from "react";
import { FaThumbsUp } from "react-icons/fa";

import { getEndorsement, setEndorsement } from "./api";
import { endorseControl } from "./panelRules";

const NEXUS_ORANGE = "#da8e35";
const GREEN = "143, 212, 143";

/** THE endorse control. One component so the same action cannot end up
 * looking like three different things - which it did: a pill on the mod
 * page, a bare square in the QAM, and a DialogButton on collections.
 *
 * `iconOnly` is the QAM's framework row, where it sits beside a full-width
 * Step button and a label would crowd it. Everywhere else it carries its
 * word, because a pill with no text is only obvious to whoever wrote it.
 */
export function EndorsePill({
  endorsed,
  busy,
  onActivate,
  iconOnly = false,
  label = "Endorse",
  endorsedLabel = "Endorsed",
}: {
  endorsed: boolean;
  busy: boolean;
  onActivate: () => void;
  iconOnly?: boolean;
  label?: string;
  endorsedLabel?: string;
}) {
  const tone = endorsed
    ? {
        background: `rgba(${GREEN}, 0.15)`,
        border: `1px solid rgba(${GREEN}, 0.5)`,
        color: `rgb(${GREEN})`,
      }
    : {
        background: "rgba(218, 142, 53, 0.15)",
        border: `1px solid ${NEXUS_ORANGE}88`,
      };
  return (
    <Focusable
      onActivate={() => {
        if (!busy) onActivate();
      }}
      style={{
        ...(iconOnly
          ? {
              width: "40px",
              height: "40px",
              marginLeft: "8px",
              flex: "0 0 auto",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              borderRadius: "4px",
            }
          : {
              // Hugs its text. As a plain block it stretched the full
              // width of the description column, which read as a banner
              // rather than a pill.
              display: "inline-flex",
              alignItems: "center",
              alignSelf: "flex-start",
              width: "fit-content",
              maxWidth: "100%",
              padding: "3px 12px",
              borderRadius: "999px",
              fontSize: "12px",
              fontWeight: 600,
              whiteSpace: "nowrap",
            }),
        // Half-lit in flight: on a handheld there is no cursor to show
        // that a press landed.
        opacity: busy ? 0.4 : 1,
        ...tone,
      }}
    >
      {iconOnly ? (
        <FaThumbsUp size={16} />
      ) : (
        <span
          style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
        >
          <FaThumbsUp size={12} />
          {endorsed ? endorsedLabel : label}
        </span>
      )}
    </Focusable>
  );
}


/** "SMAPI installed ✓" with a thumbs-up beside it, and the cooldown note
 * underneath rather than squeezed alongside. */
export function EndorsableFrameworkRow({
  text,
  gameDomain,
  modId,
  modName,
  installedMinutesAgo,
}: {
  text: string;
  gameDomain: string;
  /** Undefined for a framework with no Nexus mod page (me3, bypasses). */
  modId?: number;
  modName: string;
  /** Drives the cooldown wording. Undefined means unknown, which is the
   * common case and errs towards explaining the 15 minutes. */
  installedMinutesAgo?: number;
}) {
  const [status, setStatus] = useState<string | undefined>();
  const [version, setVersion] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (modId === undefined) return;
    let live = true;
    getEndorsement(gameDomain, modId)
      .then((r) => {
        if (!live) return;
        setStatus(r.ok ? r.status : undefined);
        setVersion(r.version ?? "");
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [gameDomain, modId]);

  const control = endorseControl(status, installedMinutesAgo);

  const onActivate = async () => {
    if (busy || modId === undefined) return;
    setBusy(true);
    try {
      const target = !control.endorsed;
      const result = await setEndorsement(gameDomain, modId, version, target);
      if (result.ok) {
        setStatus(result.status);
        toaster.toast({
          title: target ? "Endorsed!" : "Endorsement removed",
          body: modName,
        });
      } else {
        toaster.toast({ title: "Could not endorse", body: result.error ?? "" });
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ width: "100%" }}>
      <div style={{ display: "flex", alignItems: "center", width: "100%" }}>
        <span style={{ flex: "1 1 auto" }}>{text}</span>
        {control.show && (
          <EndorsePill
            endorsed={control.endorsed}
            busy={busy}
            onActivate={onActivate}
            iconOnly
          />
        )}
      </div>
      {control.hint && (
        <div
          style={{
            marginTop: "6px",
            fontSize: "11px",
            opacity: 0.6,
            lineHeight: 1.35,
          }}
        >
          {control.hint}
        </div>
      )}
    </div>
  );
}
