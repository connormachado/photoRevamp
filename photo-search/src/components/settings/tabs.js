import StorageTab from "./tabs/StorageTab";
import ThemeTab from "./tabs/ThemeTab";
import PhotosLibraryTab from "./tabs/PhotosLibraryTab";
import ExportDefaultsTab from "./tabs/ExportDefaultsTab";
import MotionDetectionTab from "./tabs/MotionDetectionTab";
import AboutTab from "./tabs/AboutTab";
import ShortcutsTab from "./tabs/ShortcutsTab";

// One entry per settings tab. Adding a real tab later (Prompts 13-19) means
// replacing that tab's component file — this array itself shouldn't need to
// change unless a tab is added, removed, or reordered.
export const SETTINGS_TABS = [
  { id: "storage", label: "Storage", component: StorageTab },
  { id: "theme", label: "Theme", component: ThemeTab },
  { id: "photos-library", label: "Photos Library", component: PhotosLibraryTab },
  { id: "export-defaults", label: "Export Defaults", component: ExportDefaultsTab },
  { id: "motion-detection", label: "Motion Detection", component: MotionDetectionTab },
  { id: "about", label: "About", component: AboutTab },
  { id: "shortcuts", label: "Shortcuts", component: ShortcutsTab },
];
