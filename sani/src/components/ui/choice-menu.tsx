import { ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export interface Choice {
  value: string;
  label: string;
}

/** A compact single-choice control for short option lists (3-8 items). */
export function ChoiceMenu({
  value,
  choices,
  onChange,
  disabled,
  label,
  align = "end",
}: {
  value: string;
  choices: Choice[];
  onChange: (value: string) => void;
  disabled?: boolean;
  /** Accessible name, e.g. "Keep technical details". */
  label: string;
  align?: "start" | "end";
}) {
  const current = choices.find((choice) => choice.value === value);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={disabled}
            aria-label={label}
            className="min-w-28 justify-between gap-2 bg-background font-normal"
          />
        }
      >
        <span className="truncate">{current?.label ?? "Choose…"}</span>
        <ChevronDown className="size-3.5 text-muted-foreground" aria-hidden="true" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align={align} className="min-w-40">
        <DropdownMenuRadioGroup value={value} onValueChange={(next) => onChange(String(next))}>
          {choices.map((choice) => (
            <DropdownMenuRadioItem key={choice.value} value={choice.value}>
              {choice.label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

