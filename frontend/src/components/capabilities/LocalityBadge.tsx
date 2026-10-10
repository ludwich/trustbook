import { IconDeviceLaptop, IconWorldUpload } from "@tabler/icons-react";
import type { Locality } from "@/api/types";
import { Badge } from "@/components/ui/badge";

export function LocalityBadge({ locality, host }: { locality: Locality | null; host?: string }) {
  if (locality === null) return null;
  if (locality === "local") {
    return (
      <Badge variant="outline" title="Runs on this computer. Your data stays here.">
        <IconDeviceLaptop aria-hidden />
        Local
      </Badge>
    );
  }
  return (
    <Badge
      variant="outline"
      className="border-warning/40 text-warning"
      title={host ? `Text is sent to ${host}.` : "Text is sent to another computer."}
    >
      <IconWorldUpload aria-hidden />
      Remote
    </Badge>
  );
}
