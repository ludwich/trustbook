import { IconSettings } from "@tabler/icons-react";
import { Link, NavLink } from "react-router";
import { cn } from "@/lib/utils";
import { ProcessingIndicator } from "./ProcessingIndicator";
import { ThemeToggle } from "./ThemeToggle";

export function Header() {
  return (
    <header className="sticky top-0 z-40 border-b bg-background/90 backdrop-blur supports-[backdrop-filter]:bg-background/75">
      <div className="mx-auto flex h-13 max-w-screen-2xl items-center gap-4 px-5">
        <Link
          to="/"
          className="flex items-center gap-2 rounded-sm text-[15px] font-semibold tracking-tight outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
        >
          <img src="/favicon.svg" alt="" className="size-5" />
          Trustbook
        </Link>
        <div className="flex flex-1 justify-center">
          <ProcessingIndicator />
        </div>
        <nav aria-label="Main" className="flex items-center gap-2">
          <NavLink
            to="/settings"
            className={({ isActive }) =>
              cn(
                "inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-sm font-medium text-muted-foreground transition-colors outline-none hover:bg-accent hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50",
                isActive && "bg-accent text-foreground",
              )
            }
          >
            <IconSettings className="size-4" aria-hidden />
            Settings
          </NavLink>
          <ThemeToggle />
        </nav>
      </div>
    </header>
  );
}
