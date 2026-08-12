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
          <Focusable
            onActivate={onActivate}
            // Square, so it reads as an icon button rather than a second
            // action competing with the Step buttons above it.
            style={{
              width: "40px",
              height: "40px",
              marginLeft: "8px",
              flex: "0 0 auto",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              borderRadius: "4px",
              // Half-lit in flight: on a handheld there is no cursor to
              // show that a press landed.
              opacity: busy ? 0.4 : 1,
              ...(control.endorsed
                ? {
                    background: `rgba(${GREEN}, 0.15)`,
                    border: `1px solid rgba(${GREEN}, 0.5)`,
                    color: `rgb(${GREEN})`,
                  }
                : {
                    background: "rgba(218, 142, 53, 0.15)",
                    border: `1px solid ${NEXUS_ORANGE}88`,
                  }),
            }}
          >
            <FaThumbsUp size={16} />
          </Focusable>
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
