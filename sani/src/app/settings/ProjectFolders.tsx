import { useState } from "react";
import { FolderPlus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ChoiceMenu } from "@/components/ui/choice-menu";
import { SettingsGroup, SettingsRow } from "@/components/settings-rows";
import { pickFolder, type ClaudeCodePermission } from "@/lib/tauri";
import { useSettings } from "./SettingsContext";

/** The run limit and project folders are one setting shared by Claude Code and ZCode. */
const PERMISSIONS: Array<{ value: ClaudeCodePermission; label: string }> = [
  { value: "read", label: "Look only" },
  { value: "edit", label: "Edit files" },
  { value: "run", label: "Edit files and run commands" },
];

/** Folder names read better as "name · parent". */
function folderParts(path: string): { name: string; parent: string } {
  const parts = path.split("/").filter(Boolean);
  return { name: parts[parts.length - 1] ?? path, parent: "/" + parts.slice(0, -1).join("/") };
}

export function RunLimitRow({ note }: { note?: string }) {
  const { snapshot, saveClaudeCode } = useSettings();
  if (!snapshot) return null;
  return (
    <SettingsRow label="What a run may do" state={note}>
      <ChoiceMenu
        label="What a run may do"
        value={snapshot.claude_code_permission}
        choices={PERMISSIONS}
        onChange={(value) => void saveClaudeCode({ permission: value as ClaudeCodePermission })}
      />
    </SettingsRow>
  );
}

export function ProjectFolders() {
  const { snapshot, saveClaudeCode, error } = useSettings();
  const [pickError, setPickError] = useState("");
  if (!snapshot) return null;

  const addFolder = async () => {
    setPickError("");
    try {
      const chosen = await pickFolder();
      if (!chosen) return;
      await saveClaudeCode({ dirs: [...snapshot.claude_code_dirs, chosen] });
    } catch (reason) {
      setPickError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  return (
    <>
      <SettingsGroup title="Project folders">
        {snapshot.claude_code_dirs.length === 0 ? (
          <SettingsRow label="No folders yet" state="Sani only works inside folders you add" />
        ) : (
          snapshot.claude_code_dirs.map((dir) => {
            const { name, parent } = folderParts(dir);
            return (
              <SettingsRow key={dir} label={name} state={parent}>
                <Button
                  size="icon-sm"
                  variant="ghost"
                  aria-label={`Remove ${name}`}
                  className="text-muted-foreground"
                  onClick={() =>
                    void saveClaudeCode({ dirs: snapshot.claude_code_dirs.filter((item) => item !== dir) })
                  }
                >
                  <X className="size-4" />
                </Button>
              </SettingsRow>
            );
          })
        )}
        <SettingsRow label="Add a project folder">
          <Button size="sm" variant="outline" onClick={() => void addFolder()}>
            <FolderPlus className="size-3.5" aria-hidden="true" />
            Choose folder
          </Button>
        </SettingsRow>
      </SettingsGroup>
      {pickError || error ? (
        <p className="mb-4 text-sm text-destructive" role="alert">
          {pickError || error}
        </p>
      ) : null}
    </>
  );
}
