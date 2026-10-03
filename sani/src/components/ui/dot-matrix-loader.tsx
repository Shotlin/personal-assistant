import * as React from "react"

import { cn } from "@/lib/utils"

type DotMatrixLoaderProps = React.ComponentPropsWithoutRef<"span"> & {
  className?: string
  label: string
}

/**
 * Paper rendering rules: 3×3 dot-matrix is the only "living mark" for
 * running work (sidebar rows, transcript tool lines). CSS shuffles the frames
 * without publishing React state on every animation step. Under
 * prefers-reduced-motion the first frame remains static.
 */
const FIRST_FRAME: readonly number[] = [1, 0, 0, 1, 1, 0, 1, 0, 1]
const DOT_ANIMATION_CLASSES = [
  "sani-dot-matrix-frame-a",
  "sani-dot-matrix-frame-b",
  "sani-dot-matrix-frame-c",
  "sani-dot-matrix-frame-d",
  "sani-dot-matrix-frame-e",
  "sani-dot-matrix-frame-f",
  "sani-dot-matrix-frame-e",
  "sani-dot-matrix-frame-f",
  "sani-dot-matrix-frame-g",
]

export function DotMatrixLoader({ className, label, ...rest }: DotMatrixLoaderProps) {
  return (
    <span
      {...rest}
      role="status"
      aria-label={label}
      title={label}
      className={cn("inline-grid size-3.5 shrink-0 grid-cols-3 grid-rows-3 gap-px", className)}
    >
      {FIRST_FRAME.map((lit, index) => (
        <span
          key={index}
          aria-hidden="true"
          className={cn(
            "sani-dot-matrix-dot size-full rounded-full bg-current",
            lit ? "sani-dot-matrix-on" : "sani-dot-matrix-off",
            DOT_ANIMATION_CLASSES[index],
          )}
        />
      ))}
    </span>
  )
}
