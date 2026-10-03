import { Button } from "@/components/ui/button";
import { DotMatrixLoader } from "@/components/ui/dot-matrix-loader";
import type { UiState } from "@/lib/tauri";

/** Voice capture in progress: nothing is sent until the user finishes it. */
export function VoiceDraft({
  state,
  partial,
  onFinish,
  onCancel,
}: {
  state: UiState;
  partial: string;
  onFinish: () => void;
  onCancel: () => void;
}) {
  const finalizing = state === "finalizing";
  return (
    <div className="flex flex-col items-end gap-1.5">
      <div className="max-w-[80%] rounded-2xl border border-dashed border-slate-7 px-3.5 py-2 text-muted-foreground select-text">
        <div className="flex items-center gap-2 text-xs">
          <DotMatrixLoader label="Listening" />
          <span>{finalizing ? "Finishing local decode" : "Listening · not sent"}</span>
        </div>
        {partial ? <p className="mt-1 leading-relaxed text-foreground">{partial}</p> : null}
      </div>
      <div className="flex gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel}>
          Cancel
          <kbd className="ml-1 font-sans text-[11px] text-muted-foreground">esc</kbd>
        </Button>
        <Button size="sm" disabled={finalizing} onClick={onFinish}>
          Finish &amp; send
        </Button>
      </div>
    </div>
  );
}
