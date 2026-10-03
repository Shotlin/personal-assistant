import { useCallback, useState } from "react";
import { Toaster } from "sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useChatEvents } from "@/chat/events";
import { Sidebar, type Section } from "./shell/Sidebar";
import ChatPage from "./pages/ChatPage";
import SettingsPage from "./settings/SettingsPage";
import ComputerControlPage from "./ComputerControlPage";
import { SettingsProvider } from "./settings/SettingsContext";
import "../styles/app.css";
// Legacy rules still used by the overlay layout editor until it is rebuilt.
import "../styles/main.css";

function MainContents() {
  const [section, setSection] = useState<Section>("chat");
  const openSettings = useCallback(() => setSection("settings"), []);
  useChatEvents(openSettings);

  return (
    <TooltipProvider delay={300}>
      <div className="flex h-full w-full bg-background text-foreground">
        <Sidebar section={section} onSection={setSection} />
        <main className="min-w-0 flex-1">
          {section === "chat" ? <ChatPage /> : section === "control" ? <ComputerControlPage /> : <SettingsPage />}
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
