import {
  IconClipboardText,
  IconFolder,
  IconPlus,
  IconUpload,
  IconWorld,
  IconWorldSearch,
} from "@tabler/icons-react";
import type { ReactNode } from "react";
import { useCapabilityReady } from "@/components/capabilities/useCapabilityReady";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { FolderPickerDialog } from "./FolderPickerDialog";
import { PasteDialog } from "./PasteDialog";
import { SearchDialog } from "./SearchDialog";
import { UploadDialog } from "./UploadDialog";
import { UrlDialog } from "./UrlDialog";

export type AddSourceKind = "upload" | "folder" | "url" | "search" | "paste";

function MenuEntry({ icon, title, note }: { icon: ReactNode; title: string; note: string }) {
  return (
    <>
      <span className="mt-0.5 text-muted-foreground">{icon}</span>
      <span className="grid">
        <span>{title}</span>
        <span className="text-xs text-muted-foreground">{note}</span>
      </span>
    </>
  );
}

export function AddSourceMenu({ onChoose }: { onChoose: (kind: AddSourceKind) => void }) {
  const search = useCapabilityReady("web_search");
  return (
    // Not modal, so a dialog opened from an item gets focus cleanly.
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <Button size="xs" variant="outline">
          <IconPlus aria-hidden />
          Add source
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuItem onSelect={() => onChoose("upload")}>
          <MenuEntry icon={<IconUpload />} title="Upload files" note="Documents and audio" />
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => onChoose("folder")}>
          <MenuEntry icon={<IconFolder />} title="Link folder" note="A folder on this computer" />
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => onChoose("url")}>
          <MenuEntry icon={<IconWorld />} title="Web page" note="A link, including YouTube" />
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => onChoose("search")}>
          <MenuEntry
            icon={<IconWorldSearch />}
            title="Search the web"
            note={search.ready || search.isLoading ? "Find pages to add" : "Set up in Settings"}
          />
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => onChoose("paste")}>
          <MenuEntry
            icon={<IconClipboardText />}
            title="Paste text"
            note="Notes, emails, anything"
          />
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function AddSourceDialogs({
  spaceId,
  open,
  onOpenChange,
  initialFiles,
}: {
  spaceId: string;
  open: AddSourceKind | null;
  onOpenChange: (kind: AddSourceKind | null) => void;
  initialFiles: File[];
}) {
  const props = (kind: AddSourceKind) => ({
    spaceId,
    open: open === kind,
    onOpenChange: (o: boolean) => onOpenChange(o ? kind : null),
  });
  return (
    <>
      <UploadDialog {...props("upload")} initialFiles={initialFiles} />
      <FolderPickerDialog {...props("folder")} />
      <UrlDialog {...props("url")} />
      <SearchDialog {...props("search")} />
      <PasteDialog {...props("paste")} />
    </>
  );
}
