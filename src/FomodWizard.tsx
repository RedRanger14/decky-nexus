// The FOMOD install wizard: steps -> groups -> options, driven by the
// backend-parsed wizard JSON. Flags from selected options gate later
// steps' visibility (evaluated here); the backend re-derives flags
// authoritatively when applying.
import { DialogButton, Focusable, GamepadButton, ModalRoot } from "@decky/ui";
import { useEffect, useMemo, useRef, useState } from "react";

import { NEXUS_ORANGE } from "./theme";

export interface FomodDepTree {
  kind: "group" | "flag" | "const";
  op?: "and" | "or";
  conds?: FomodDepTree[];
  name?: string;
  value?: string | boolean;
}

export interface FomodPlugin {
  id: string;
  name: string;
  description: string;
  type: string;
  flags: Record<string, string>;
}

export interface FomodGroup {
  name: string;
  type: string;
  plugins: FomodPlugin[];
}

export interface FomodStep {
  name: string;
  visible: FomodDepTree | null;
  groups: FomodGroup[];
}

export interface FomodWizardData {
  moduleName: string;
  steps: FomodStep[];
}

function evalDeps(tree: FomodDepTree | null, flags: Record<string, string>): boolean {
  if (!tree) return true;
  if (tree.kind === "const") return Boolean(tree.value);
  if (tree.kind === "flag") return flags[tree.name ?? ""] === tree.value;
  const results = (tree.conds ?? []).map((c) => evalDeps(c, flags));
  if (results.length === 0) return true;
  return tree.op === "or" ? results.some(Boolean) : results.every(Boolean);
}

const RADIO_TYPES = new Set(["SelectExactlyOne", "SelectAtMostOne"]);

function defaultSelection(steps: FomodStep[]): Set<string> {
  const sel = new Set<string>();
  for (const step of steps) {
    for (const group of step.groups) {
      if (group.type === "SelectAll") {
        group.plugins.forEach((p) => sel.add(p.id));
        continue;
      }
      const required = group.plugins.filter((p) => p.type === "Required");
      const recommended = group.plugins.filter((p) => p.type === "Recommended");
      if (RADIO_TYPES.has(group.type)) {
        const pick =
          required[0] ??
          recommended[0] ??
          (group.type === "SelectExactlyOne" ? group.plugins[0] : undefined);
        if (pick) sel.add(pick.id);
      } else {
        required.forEach((p) => sel.add(p.id));
        recommended.forEach((p) => sel.add(p.id));
        if (group.type === "SelectAtLeastOne" && sel.size === 0 && group.plugins[0]) {
          sel.add(group.plugins[0].id);
        }
      }
    }
  }
  return sel;
}

export function FomodWizardModal({
  wizard,
  onInstall,
  closeModal,
}: {
  wizard: FomodWizardData;
  onInstall: (selectedIds: string[]) => void;
  closeModal?: () => void;
}) {
  const [selected, setSelected] = useState<Set<string>>(() =>
    defaultSelection(wizard.steps)
  );
  const [stepIdx, setStepIdx] = useState(0);
  const bodyRef = useRef<HTMLDivElement>(null);

  // Each step starts at the top - carrying the previous step's scroll
  // position forces the user to scroll back up every time.
  useEffect(() => {
    // The gamepad focus scrolling happens in the MODAL's container, not
    // our inner div - walk every scrollable ancestor back to the top.
    let el: HTMLElement | null = bodyRef.current;
    while (el) {
      if (el.scrollTop) el.scrollTop = 0;
      el = el.parentElement;
    }
    if (bodyRef.current) bodyRef.current.scrollTop = 0;
  }, [stepIdx]);

  // Flags accumulate from selected plugins of steps BEFORE the current
  // one; a step's visibility is judged against them.
  const flagsBefore = (upTo: number): Record<string, string> => {
    const flags: Record<string, string> = {};
    wizard.steps.slice(0, upTo).forEach((step) => {
      if (!evalDeps(step.visible, flags)) return;
      step.groups.forEach((g) =>
        g.plugins.forEach((p) => {
          if (selected.has(p.id)) Object.assign(flags, p.flags);
        })
      );
    });
    return flags;
  };

  const visibleSteps = useMemo(() => {
    const out: number[] = [];
    wizard.steps.forEach((step, i) => {
      if (evalDeps(step.visible, flagsBefore(i))) out.push(i);
    });
    return out;
  }, [selected, wizard.steps]);

  const current = visibleSteps[stepIdx];
  const step = current !== undefined ? wizard.steps[current] : undefined;
  const isLast = stepIdx >= visibleSteps.length - 1;

  const toggle = (group: FomodGroup, plugin: FomodPlugin) => {
    if (plugin.type === "Required" || plugin.type === "NotUsable") return;
    if (group.type === "SelectAll") return;
    setSelected((prev) => {
      const next = new Set(prev);
      if (RADIO_TYPES.has(group.type)) {
        group.plugins.forEach((p) => next.delete(p.id));
        if (!prev.has(plugin.id) || group.type === "SelectExactlyOne") {
          next.add(plugin.id);
        }
      } else if (next.has(plugin.id)) {
        next.delete(plugin.id);
      } else {
        next.add(plugin.id);
      }
      return next;
    });
  };

  const finish = () => {
    // Only selections in VISIBLE steps count.
    const ids: string[] = [];
    const flags: Record<string, string> = {};
    wizard.steps.forEach((s) => {
      if (!evalDeps(s.visible, flags)) return;
      s.groups.forEach((g) =>
        g.plugins.forEach((p) => {
          if (selected.has(p.id)) {
            ids.push(p.id);
            Object.assign(flags, p.flags);
          }
        })
      );
    });
    closeModal?.();
    onInstall(ids);
  };

  // Power-user shortcuts: 39-step wizards exist (JK's Interiors) and
  // scrolling to the Next button on every page is controller torture.
  const goNext = () => (isLast ? finish() : setStepIdx(stepIdx + 1));
  const goBack = () => setStepIdx(Math.max(0, stepIdx - 1));

  return (
    <ModalRoot closeModal={closeModal}>
      <Focusable
        onButtonDown={(evt: CustomEvent) => {
          const button = (evt as any)?.detail?.button;
          if (button === GamepadButton.BUMPER_RIGHT) goNext();
          else if (button === GamepadButton.BUMPER_LEFT) goBack();
        }}
      >
      <h3 style={{ marginTop: 0, marginBottom: "2px" }}>
        {wizard.moduleName || "Mod options"}
      </h3>
      {step && (
        <div style={{ fontSize: "12.5px", opacity: 0.7, marginBottom: "8px" }}>
          {step.name} · step {stepIdx + 1} of {visibleSteps.length}
          <span style={{ float: "right", opacity: 0.8 }}>
            LB ← step · step → RB
          </span>
        </div>
      )}
      <div
        ref={bodyRef}
        style={{ maxHeight: "52vh", overflowY: "auto", paddingRight: "4px" }}
      >
      {step?.groups.map((group, gi) => (
        <div key={gi} style={{ marginBottom: "10px" }}>
          <div style={{ fontSize: "13px", fontWeight: 600, marginBottom: "4px" }}>
            {group.name}
            <span style={{ opacity: 0.55, fontWeight: 400, fontSize: "11.5px" }}>
              {RADIO_TYPES.has(group.type)
                ? "  · pick one"
                : group.type === "SelectAll"
                ? "  · all included"
                : "  · pick any"}
            </span>
          </div>
          <Focusable style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
            {group.plugins.map((plugin) => {
              const on = selected.has(plugin.id);
              const locked =
                plugin.type === "Required" ||
                plugin.type === "NotUsable" ||
                group.type === "SelectAll";
              return (
                <Focusable
                  key={plugin.id}
                  onActivate={() => toggle(group, plugin)}
                  style={{
                    padding: "6px 10px",
                    borderRadius: "4px",
                    background: on
                      ? "rgba(218, 142, 53, 0.18)"
                      : "rgba(255,255,255,0.05)",
                    border: on
                      ? `1px solid ${NEXUS_ORANGE}aa`
                      : "1px solid transparent",
                    opacity: plugin.type === "NotUsable" ? 0.4 : locked ? 0.85 : 1,
                  }}
                >
                  <div style={{ fontSize: "13px" }}>
                    {on ? "◉ " : "○ "}
                    {plugin.name}
                    {plugin.type === "Required" && (
                      <span style={{ opacity: 0.6 }}> (required)</span>
                    )}
                  </div>
                  {plugin.description && (
                    <div style={{ fontSize: "11.5px", opacity: 0.65 }}>
                      {plugin.description.length > 160
                        ? plugin.description.slice(0, 160) + "…"
                        : plugin.description}
                    </div>
                  )}
                </Focusable>
              );
            })}
          </Focusable>
        </div>
      ))}
      </div>
      <Focusable style={{ display: "flex", gap: "10px", marginTop: "10px" }}>
        <DialogButton
          disabled={stepIdx === 0}
          onClick={goBack}
          style={{ flex: 1, minWidth: 0 }}
        >
          ← Back
        </DialogButton>
        <DialogButton onClick={goNext} style={{ flex: 1, minWidth: 0 }}>
          {isLast ? "Install with these options" : "Next →"}
        </DialogButton>
      </Focusable>
      </Focusable>
    </ModalRoot>
  );
}
