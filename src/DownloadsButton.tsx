// Downloads shortcut for the pushed detail pages (a mod, a collection).
// Those pages sit on top of the nav stack and have no tab strip, so the
// only route to Downloads was backing out to the Store first - painful
// exactly when it matters, mid-collection-install.
import { DialogButton, Navigation } from "@decky/ui";
import { useEffect, useState } from "react";
import { FaDownload } from "react-icons/fa";

import {
  getAggregateDownloadPercent,
  getCollectionRun,
  getDownloads,
  subscribeDownloads,
} from "./state";
import { DOWNLOADS_ROUTE, noteTabPush } from "./Tabs";
import { ACTION_BUTTON_ICON, NEXUS_ORANGE } from "./theme";

/** Icon-only on purpose: the action rows it joins are already crowded
 * with "Install optional", "Finish setup", "Uninstall" and friends, and
 * another labelled button would crush them. Shows live progress when
 * something is downloading, so it doubles as the at-a-glance status. */
export function DownloadsButton() {
  const [, bump] = useState(0);
  useEffect(() => subscribeDownloads(() => bump((n) => n + 1)), []);

  const active = getDownloads().length;
  const percent = getAggregateDownloadPercent(getCollectionRun());
  const busy = active > 0;

  return (
    <DialogButton
      onClick={() => {
        noteTabPush();
        Navigation.Navigate(DOWNLOADS_ROUTE);
      }}
      style={{
        ...ACTION_BUTTON_ICON,
        ...(busy ? { color: NEXUS_ORANGE, fontWeight: 600 } : {}),
      }}
    >
      <FaDownload />
      {busy && (
        <span style={{ fontSize: "12px" }}>
          {percent !== undefined ? `${percent}%` : active}
        </span>
      )}
    </DialogButton>
  );
}
