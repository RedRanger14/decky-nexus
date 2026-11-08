// Settings tab: download pipeline tuning. Controller-first - every
// control is a slider (d-pad friendly), every risk is stated in plain
// words next to the control that carries it. Values clamp server-side
// too, so nothing here can wedge the backend.
import {
  DropdownItem,
  Focusable,
  ScrollPanelGroup,
  SliderField,
} from "@decky/ui";
import { useEffect, useState } from "react";

import { UserPrefs, getUserPrefs, setUserPrefs } from "./api";
import { NEXUS_ORANGE, PAGE_SCROLLER } from "./theme";
import { TabBar, exitTabsToQam, handleTabButtons } from "./Tabs";
import {
  LOCALES,
  Locale,
  getLocale,
  setLocale,
  t,
  useT,
} from "./i18n";

const Scroller: any = ScrollPanelGroup;

const DEFAULTS: UserPrefs = {
  parallel_downloads: 4,
  prefetch_window: 8,
  extract_ahead: 2,
  speed_cap_mbps: 0,
  min_free_gb: 5,
  mod_language: "english",
};

// Tags verified live on the search index (2026-08-05). "english" is an
// EXCLUSION mode: most mods are untagged originals, so requiring the
// English tag would hide three quarters of the catalog.
const LANGUAGE_OPTIONS: { value: string; label: string }[] = [
  { value: "english", label: "English (hide translations)" },
  { value: "all", label: "All languages" },
  ...[
    "French",
    "German",
    "Spanish",
    "Italian",
    "Russian",
    "Polish",
    "Portuguese",
    "Mandarin",
    "Japanese",
    "Korean",
    "Czech",
    "Turkish",
    "Ukrainian",
    "Hungarian",
    "Dutch",
  ].map((l) => ({ value: l, label: `${l} (translations only)` })),
];

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
  // Re-render this page when the language is changed from the control below.
  useT();
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
        style={PAGE_SCROLLER}
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
          <h2 style={{ margin: 0 }}>{t("Settings")}</h2>
          <span
            style={{
              fontSize: "12px",
              color: NEXUS_ORANGE,
              opacity: saved ? 1 : 0,
              transition: "opacity 0.4s ease",
            }}
          >
            {t("✓ Saved")}</span>
        </div>
        <div style={{ fontSize: "12.5px", opacity: 0.65, marginBottom: "18px" }}>
          {t("Changes save instantly and apply to the next download.")}</div>

        {!prefs ? (
          <div style={{ opacity: 0.8 }}>{t("Loading…")}</div>
        ) : (
          <>
            <Section
              title={t("Language")}
              blurb={t("what the plugin speaks")}
            >
              <DropdownItem
                label={t("Interface language")}
                description={t("Choose the language of the plugin's interface.")}
                menuLabel={t("Interface language")}
                rgOptions={LOCALES.map((l) => ({
                  data: l.value,
                  label: l.label,
                }))}
                selectedOption={getLocale()}
                onChange={(opt: { data: string }) =>
                  setLocale(opt.data as Locale)
                }
              />
            </Section>

            <Section
              title={t("Download pipeline")}
              blurb={t("how collections use your connection")}
            >
              <SliderField
                label={t("Parallel downloads")}
                description={t("Mods downloaded at once during a collection install. More saturates fast connections; beyond ~6 the Nexus Mods API may rate-limit bursts (retried automatically, but slower overall).")}
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
                  {t("⚠ High parallelism can trip API rate limits on big collections - if you see slowdowns, 4-6 is the sweet spot.")}</Caution>
              )}
              <SliderField
                label={t("Download-ahead buffer")}
                description={t("Archives fetched ahead of the installer. Bigger keeps fast connections busy through slow installs, but every buffered archive sits on disk until installed.")}
                value={prefs.prefetch_window}
                min={2}
                max={16}
                step={1}
                showValue={true}
                onChange={(v: number) => update({ prefetch_window: v })}
              />
              {prefs.prefetch_window > 10 && (
                <Caution>
                  {t("⚠ A large buffer can briefly hold several GB of archives - fine on a roomy SSD, risky when the drive is nearly full. The free-space floor below is your safety net.")}</Caution>
              )}
              <SliderField
                label={t("Unpack ahead")}
                description={t("Mods unpacked while the previous one is still being installed. Unpacking is the slow half of an install and uses a different part of your hardware than the download, so overlapping them is close to free. Mods are still INSTALLED one at a time, in the collection's order - that order is what decides which mod wins a conflict. Set to 0 for the old one-at-a-time behaviour.")}
                value={prefs.extract_ahead}
                min={0}
                max={4}
                step={1}
                notchCount={5}
                showValue={true}
                onChange={(v: number) => update({ extract_ahead: v })}
              />
              {prefs.extract_ahead === 0 && (
                <Caution>
                  {t("Unpacking ahead is off - installs run strictly one at a time. Turn it back up if collections feel slow.")}</Caution>
              )}
            </Section>

            <Section title={t("Content")} blurb={t("what the mod browser shows")}>
              <DropdownItem
                label={t("Mod language")}
                description={t("English hides mods tagged as translations. Picking a language shows ONLY mods tagged with it - handy for finding translations of your games.")}
                menuLabel={t("Mod language")}
                rgOptions={LANGUAGE_OPTIONS.map((o) => ({
                  data: o.value,
                  label:
                    o.value === "english" || o.value === "all"
                      ? t(o.label)
                      : t("{lang} (translations only)", {
                          lang: t(o.value),
                        }),
                }))}
                selectedOption={prefs.mod_language ?? "english"}
                onChange={(opt: { data: string }) =>
                  update({ mod_language: opt.data })
                }
              />
            </Section>

            <Section title={t("Bandwidth")} blurb={t("be kind to the rest of your network")}>
              <SliderField
                label={
                  prefs.speed_cap_mbps === 0
                    ? t("Speed limit: unlimited")
                    : t("Speed limit: {n} MB/s", {
                        n: prefs.speed_cap_mbps,
                      })
                }
                description={t("Total cap shared across all parallel downloads. Set it if downloads starve game streaming or others on your network; 0 means no limit.")}
                value={prefs.speed_cap_mbps}
                min={0}
                max={200}
                step={5}
                showValue={false}
                onChange={(v: number) => update({ speed_cap_mbps: v })}
              />
            </Section>

            <Section title={t("Disk safety")} blurb={t("downloads stop before your drive fills")}>
              <SliderField
                label={t("Keep at least {n} GB free", {
                  n: prefs.min_free_gb,
                })}
                description={t("Downloads pause safely when free space falls below this. SteamOS misbehaves badly on a full drive - keep a real margin.")}
                value={prefs.min_free_gb}
                min={1}
                max={50}
                step={1}
                showValue={false}
                onChange={(v: number) => update({ min_free_gb: v })}
              />
            </Section>

            <div style={{ fontSize: "12px", opacity: 0.55, lineHeight: 1.5 }}>
              {t("Installs run one at a time by design - they share the game's folders and load-order files, and parallel writes there can corrupt a modded setup. The pipeline keeps your connection busy the whole time instead: downloading ahead is where the speed comes from.")}</div>
          </>
        )}
      </Scroller>
    </Focusable>
  );
}
