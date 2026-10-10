import {
  IconAlertTriangle,
  IconCircleCheck,
  IconCircleDashed,
  IconCircleOff,
} from "@tabler/icons-react";
import type { CapabilityStatus } from "@/api/types";
import { STATUS_LABELS } from "./statusLabels";
import { Badge } from "@/components/ui/badge";

export function StatusPill({ status, title }: { status: CapabilityStatus; title?: string }) {
  const label = STATUS_LABELS[status];
  switch (status) {
    case "ready":
      return (
        <Badge variant="success" title={title} data-status={status}>
          <IconCircleCheck aria-hidden />
          {label}
        </Badge>
      );
    case "not_configured":
      return (
        <Badge variant="warning" title={title} data-status={status}>
          <IconCircleDashed aria-hidden />
          {label}
        </Badge>
      );
    case "error":
      return (
        <Badge variant="danger" title={title} data-status={status}>
          <IconAlertTriangle aria-hidden />
          {label}
        </Badge>
      );
    case "disabled":
      return (
        <Badge variant="muted" title={title} data-status={status}>
          <IconCircleOff aria-hidden />
          {label}
        </Badge>
      );
  }
}
