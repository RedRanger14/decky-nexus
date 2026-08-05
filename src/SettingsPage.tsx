// Settings tab: download pipeline tuning. Controller-first - every
// control is a slider (d-pad friendly), every risk is stated in plain
// words next to the control that carries it. Values clamp server-side
// too, so nothing here can wedge the backend.
import { Focusable, ScrollPanelGroup, SliderField } from "@decky/ui";
import { useEffect, useState } from "react";

import { UserPrefs, getUserPrefs, setUserPrefs } from "./api";
import { NEXUS_ORANGE } from "./theme";
import { TabBar, exitTabsToQam, handleTabButtons } from "./Tabs";

const Scroller: any = ScrollPanelGroup;

const DEFAULTS: UserPrefs = {
  parallel_downloads: 4,
  prefetch_window: 8,
  speed_cap_mbps: 0,
  min_free_gb: 5,
};

/** Section header: brand accent bar + title + one-line purpose. */
function Section({
  title,
  blurb,
  children,
}: {
  title: string;
  blurb: string;
  children: React.ReactNode;
}) {
  return (
    <div style={{ marginBottom: "26px" }}>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: "10px",
          marginBottom: "2px",
        }}
      >
        <div
          style={{
            width: "4px",
            height: "18px",
            background: NEXUS_ORANGE,
            borderRadius: "2px",
            alignSelf: "center",
          }}
        />
        <h3 style={{ margin: 0 }}>{title}</h3>
        <span style={{ fontSize: "12px", opacity: 0.6 }}>{blurb}</span>
      </div>
      <div
        style={{
          background: "rgba(255,255,255,0.04)",
          border: "1px solid rgba(255,255,255,0.07)",
          borderRadius: "10px",
          padding: "6px 16px",
        }}
      >
        {children}
      </div>
    </div>
  );
}

/** Amber caution strip for settings whose upper range has real risks. */
function Caution({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        padding: "7px 10px",
        margin: "2px 0 10px",
        background: "rgba(255, 200, 60, 0.10)",
        borderLeft: "3px solid #ffc83c",
        borderRadius: "4px",
        fontSize: "12px",
        lineHeight: "1.45",
        opacity: 0.9,
      }}
    >
      {children}
    </div>
  );
}

export function SettingsPage() {
  const [prefs, setPrefs] = useState<UserPrefs | undefined>();
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    getUserPrefs().then((r) => setPrefs(r.prefs ?? DEFAULTS));
  }, []);

  const update = (patch: Partial<UserPrefs>) => {
    if (!prefs) return;
    const next = { ...prefs, ...patch };
    setPrefs(next);
    setUserPrefs(patch).then((r) => {
      if (r.ok && r.prefs) setPrefs(r.prefs);
      setSaved(true);
      setTimeout(() => setSaved(false), 1200);
    });
  };

  return (
    <Focusable
      onButtonDown={handleTabButtons("settings")}
      onCancel={exitTabsToQam}
      style={{ marginTop: "40px", height: "calc(100% - 40px)" }}
    >
      <Scroller
        focusable={false}
        onButtonDown={handleTabButtons("settings")}
        style={{
          height: "100%",
          overflowY: "auto",
          padding: "0 24px 110px",
          scrollPaddingBottom: "110px",
        }}
      >
        <TabBar currentId="settings" />
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            margin: "12px 0 4px",
          }}
        >
          <h2 style={{ margin: 0 }}>Settings</h2>
          <span
            style={{
              fontSize: "12px",
              color: NEXUS_ORANGE,
              opacity: saved ? 1 : 0,
              transition: "opacity 0.4s ease",
            }}
          >
            ✓ Saved
          </span>
        </div>
        <div style={{ fontSize: "12.5px", opacity: 0.65, marginBottom: "18px" }}>
          Changes save instantly and apply to the next download.
        </div>

        {!prefs ? (
          <div style={{ opacity: 0.8 }}>Loading…</div>
        ) : (
          <>
            <Section
              title="Download pipeline"
              blurb="how collections use your connection"
            >
              <SliderField
                label="Parallel downloads"
                description="Mods downloaded at once during a collection install. More saturates fast connections; beyond ~6 the Nexus Mods API may rate-limit bursts (retried automatically, but slower overall)."
                value={prefs.parallel_downloads}
                min={1}
                max={8}
                step={1}
                notchCount={8}
                showValue={true}
                onChange={(v: number) => update({ parallel_downloads: v })}
              />
              {prefs.parallel_downloads > 6 && (
                <Caution>
                  ⚠ High parallelism can trip API rate limits on big
                  collections - if you see slowdowns, 4-6 is the sweet spot.
                </Caution>
              )}
              <SliderField
                label="Download-ahead buffer"
                description="Archives fetched ahead of the installer. Bigger keeps fast connections busy through slow installs, but every buffered archive sits on disk until installed."
                value={prefs.prefetch_window}
                min={2}
                max={16}
                step={1}
                showValue={true}
                onChange={(v: number) => update({ prefetch_window: v })}
              />
              {prefs.prefetch_window > 10 && (
                <Caution>
                  ⚠ A large buffer can briefly hold several GB of archives -
                  fine on a roomy SSD, risky when the drive is nearly full.
                  The free-space floor below is your safety net.
                </Caution>
              )}
            </Section>

            <Section title="Bandwidth" blurb="be kind to the rest of your network">
              <SliderField
                label={
                  prefs.speed_cap_mbps === 0
                    ? "Speed limit: unlimited"
                    : `Speed limit: ${prefs.speed_cap_mbps} MB/s`
                }
                description="Total cap shared across all parallel downloads. Set it if downloads starve game streaming or others on your network; 0 means no limit."
                value={prefs.speed_cap_mbps}
                min={0}
                max={200}
                step={5}
                showValue={false}
                onChange={(v: number) => update({ speed_cap_mbps: v })}
              />
            </Section>

            <Section title="Disk safety" blurb="downloads stop before your drive fills">
              <SliderField
                label={`Keep at least ${prefs.min_free_gb} GB free`}
                description="Downloads pause safely when free space falls below this. SteamOS misbehaves badly on a full drive - keep a real margin."
                value={prefs.min_free_gb}
                min={1}
                max={50}
                step={1}
                showValue={false}
                onChange={(v: number) => update({ min_free_gb: v })}
              />
            </Section>

            <div style={{ fontSize: "12px", opacity: 0.55, lineHeight: 1.5 }}>
              Installs run one at a time by design - they share the game's
              folders and load-order files, and parallel writes there can
              corrupt a modded setup. The pipeline keeps your connection
              busy the whole time instead: downloading ahead is where the
              speed comes from.
            </div>
          </>
        )}
      </Scroller>
    </Focusable>
  );
}
