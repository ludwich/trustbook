import {
  IconBrandYoutube,
  IconClipboardText,
  IconFile,
  IconFolder,
  IconMicrophone,
  IconWorld,
  IconWorldSearch,
} from "@tabler/icons-react";
import type { Source } from "@/api/types";

export function SourceTypeIcon({ source, className }: { source: Source; className?: string }) {
  const props = { className, "aria-hidden": true } as const;
  switch (source.type) {
    case "folder":
      return <IconFolder {...props} />;
    case "url":
      return <IconWorld {...props} />;
    case "youtube":
      return <IconBrandYoutube {...props} />;
    case "search":
      return <IconWorldSearch {...props} />;
    case "paste":
      return <IconClipboardText {...props} />;
    default:
      return source.group === "transcripts" ? (
        <IconMicrophone {...props} />
      ) : (
        <IconFile {...props} />
      );
  }
}
