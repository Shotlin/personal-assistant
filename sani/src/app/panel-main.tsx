import PanelApp from "./PanelApp";
import { SettingsProvider } from "./settings/SettingsContext";
import { mountApp } from "../lib/boot";

mountApp(<SettingsProvider><PanelApp /></SettingsProvider>, "panel");
