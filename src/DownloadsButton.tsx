// Downloads shortcut for the pushed detail pages (a mod, a collection).
// Those pages sit on top of the nav stack and have no tab strip, so the
// only route to Downloads was backing out to the Store first - painful
// exactly when it matters, mid-collection-install.
import { DialogButton } from "@decky/ui";
import { useEffect, useState } from "react";
import { FaDownload } from "react-icons/fa";

import {
  getAggregateDownloadPercent,
  getCollectionRun,
  getDownloads,
  subscribeDownloads,
} from "./state";
import { DOWNLOADS_ROUTE, pushOurPage } from "./Tabs";
import { ACTION_BUTTON, NEXUS_ORANGE } from "./theme";

/** Says where it goes, not what it is: an icon-only download glyph next
 * to an Install button reads as a second download button. Sized like
 * every other action in the row. Shows live progress when something is
 * downloading, so it doubles as the at-a-glance status. */
export function DownloadsButton() {
  const [, bump] = useState(0);
  useEffect(() => subscribeDownloads(() => bump((n) => n + 1)), []);

  const active = getDownloads().length;
  const percent = getAggregateDownloadPercent(getCollectionRun());
  const busy = active > 0;

  return (
    <DialogButton
      // pushOurPage counts it; the old noteTabPush() alongside made it
      // count twice, and an over-count pops Steam's own screens on exit.
      onClick={() => pushOurPage(DOWNLOADS_ROUTE)}
      style={{
        ...ACTION_BUTTON,
        ...(busy ? { color: NEXUS_ORANGE, fontWeight: 600 } : {}),
      }}
    >
      <span
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: "7px",
          whiteSpace: "nowrap",
        }}
      >
        <FaDownload />
        {busy && percent !== undefined
          ? `Downloads ${percent}%`
          : "Go to downloads"}
      </span>
    </DialogButton>
  );
}
