import { useCallback, useState } from "react";
import { Toaster } from "sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useChatEvents } from "@/chat/events";
import { Sidebar, type Section } from "./shell/Sidebar";
import ChatPage from "./pages/ChatPage";
import SettingsPage, { SETTINGS_CATEGORIES, type SettingsCategory } from "./settings/SettingsPage";
import ComputerControlPage from "./ComputerControlPage";
import { SettingsProvider } from "./settings/SettingsContext";
import { PREVIEW_BUILD } from "../dev/enabled";
import "../styles/app.css";
// Legacy rules still used by the overlay layout editor until it is rebuilt.
import "../styles/main.css";

/** Preview only: `?open=settings` / `?open=control` lands on that page. */
function initialSection(): Section {
  if (!PREVIEW_BUILD) return "chat";
  const open = new URLSearchParams(window.location.search).get("open");
  return open === "settings" || open === "control" ? open : "chat";
}

/** Preview only: `?category=Claude Code` opens that settings page. */
function initialCategory(): SettingsCategory {
  const wanted = PREVIEW_BUILD ? new URLSearchParams(window.location.search).get("category") : null;
  return (SETTINGS_CATEGORIES as readonly string[]).includes(wanted ?? "")
    ? (wanted as SettingsCategory)
    : "General";
}

function MainContents() {
  const [section, setSection] = useState<Section>(initialSection);
  const [category, setCategory] = useState<SettingsCategory>(initialCategory);
  const openSettings = useCallback(() => setSection("settings"), []);
  useChatEvents(openSettings);

  return (
    <TooltipProvider delay={300}>
      <div className="flex h-full w-full bg-background text-foreground">
        <Sidebar
          section={section}
          onSection={(next) => {
            if (next === "settings") setCategory("General");
            setSection(next);
          }}
          onOpenClaudeCode={() => {
            setCategory("Claude Code");
            setSection("settings");
          }}
          onOpenZCode={() => {
            setCategory("ZCode");
            setSection("settings");
          }}
        />
        <main className="min-w-0 flex-1">
          {section === "chat" ? <ChatPage /> : section === "control" ? <ComputerControlPage /> : <SettingsPage initialCategory={category} />}
        </main>
      </div>
      <Toaster position="bottom-right" theme="light" />
    </TooltipProvider>
  );
}

export default function MainApp() {
  return (
    <SettingsProvider>
      <MainContents />
    </SettingsProvider>
  );
}
