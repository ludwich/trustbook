import { createContext, useContext } from "react";
import type { Citation } from "@/api/types";

export interface CitationViewer {
  /** Opens the source viewer at the cited passage. */
  open: (citation: Citation) => void;
}

export const CitationViewerContext = createContext<CitationViewer>({ open: () => {} });

export function useCitationViewer(): CitationViewer {
  return useContext(CitationViewerContext);
}
