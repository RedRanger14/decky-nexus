// Option-style archives ship several alternative folders (a manual-choice
// mini-FOMOD). The backend lists them; the user picks one to install.
// Shared by the mod detail page and the collection "Finish setup" flow.
import { ButtonItem, ModalRoot } from "@decky/ui";

export function PayloadChoiceModal({
  modName,
  options,
  labels,
  onPick,
  closeModal,
  allowMerge,
}: {
  modName: string;
  options: string[];
  /** What to SHOW for each option. Frostbite archives name their variants in
   * the file name, so the raw options are three near-identical paths whose
   * one differing word is at the end - unreadable on a TV. The value handed
   * back is still the option itself. */
  labels?: string[];
  onPick: (option: string) => void;
  closeModal?: () => void;
  /** Offer "install everything"? Replacer packs want it; HD2 variant
   * archives must not see it - their folders all patch the same file. */
  allowMerge?: boolean;
}) {
  return (
    <ModalRoot closeModal={closeModal}>
      <h3 style={{ marginTop: 0 }}>{modName}: choose a version</h3>
      <div style={{ fontSize: "13px", opacity: 0.9, marginBottom: "8px" }}>
        This mod's archive has several parts. Some mods offer alternatives
        to pick between; others are a set that belongs together. (Check the
        mod's description if you're unsure.)
      </div>
      {options.length > 1 && allowMerge !== false && (
        <ButtonItem
          layout="below"
          description="Multi-part mods want all of them: The Mandalorian ships a base, text and weapon part"
          onClick={() => {
            closeModal?.();
            onPick("*");
          }}
        >
          Install all {options.length} parts
        </ButtonItem>
      )}
      {options.map((opt, i) => (
        <ButtonItem
          key={opt}
          layout="below"
          onClick={() => {
            closeModal?.();
            onPick(opt);
          }}
        >
          {labels?.[i] || opt}
        </ButtonItem>
      ))}
    </ModalRoot>
  );
}
