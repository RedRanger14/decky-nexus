// Option-style archives ship several alternative folders (a manual-choice
// mini-FOMOD). The backend lists them; the user picks one to install.
// Shared by the mod detail page and the collection "Finish setup" flow.
import { ButtonItem, ModalRoot } from "@decky/ui";

export function PayloadChoiceModal({
  modName,
  options,
  onPick,
  closeModal,
}: {
  modName: string;
  options: string[];
  onPick: (option: string) => void;
  closeModal?: () => void;
}) {
  return (
    <ModalRoot closeModal={closeModal}>
      <h3 style={{ marginTop: 0 }}>{modName}: choose a version</h3>
      <div style={{ fontSize: "13px", opacity: 0.9, marginBottom: "8px" }}>
        This mod's archive offers alternative folders — pick the one to
        install. (Check the mod's description if you're unsure.)
      </div>
      {options.length > 1 && (
        <ButtonItem
          layout="below"
          description="Replacer packs usually want all folders combined"
          onClick={() => {
            closeModal?.();
            onPick("*");
          }}
        >
          Install everything (merge all {options.length} folders)
        </ButtonItem>
      )}
      {options.map((opt) => (
        <ButtonItem
          key={opt}
          layout="below"
          onClick={() => {
            closeModal?.();
            onPick(opt);
          }}
        >
          {opt}
        </ButtonItem>
      ))}
    </ModalRoot>
  );
}
