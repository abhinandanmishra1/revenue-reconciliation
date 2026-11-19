"use client";

import { useRef, useState } from "react";
import { FileText, Upload, X } from "lucide-react";

import { cn } from "@/lib/utils";

export const MAX_BYTES = 2 * 1024 * 1024;

/** Client-side checks for fast feedback only; the API re-validates everything. */
export function checkFile(file: File): string | null {
  if (!file.name.toLowerCase().endsWith(".csv")) return "Choose a .csv file.";
  if (file.size === 0) return "This file is empty.";
  if (file.size > MAX_BYTES) return "This file is larger than 2 MB.";
  return null;
}

type Props = {
  label: string;
  hint: string;
  file: File | null;
  error: string | null;
  disabled?: boolean;
  onChange: (file: File | null) => void;
};

export function FileDrop({ label, hint, file, error, disabled, onChange }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);

  return (
    <div className="grid gap-1.5">
      <span className="text-sm font-medium">{label}</span>
      <div
        role="button"
        tabIndex={0}
        aria-label={`Choose ${label.toLowerCase()}`}
        onClick={() => !disabled && input.current?.click()}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && !disabled && input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          if (!disabled && e.dataTransfer.files[0]) onChange(e.dataTransfer.files[0]);
        }}
        className={cn(
          "flex min-h-28 cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-4 text-center text-sm transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
          over ? "border-primary bg-muted" : "hover:bg-muted/50",
          error && "border-destructive",
          disabled && "cursor-not-allowed opacity-60",
        )}
      >
        {file ? (
          <div className="flex items-center gap-2">
            <FileText className="size-4 shrink-0" />
            <span className="max-w-56 truncate font-medium">{file.name}</span>
            <span className="text-muted-foreground">{(file.size / 1024).toFixed(1)} KB</span>
            {!disabled && (
              <button
                type="button"
                aria-label="Remove file"
                className="rounded p-0.5 hover:bg-accent"
                onClick={(e) => {
                  e.stopPropagation();
                  onChange(null);
                }}
              >
                <X className="size-4" />
              </button>
            )}
          </div>
        ) : (
          <>
            <Upload className="size-5 text-muted-foreground" />
            <span>
              Drop a CSV here or <span className="font-medium underline">browse</span>
            </span>
            <span className="text-xs text-muted-foreground">{hint}</span>
          </>
        )}
        <input
          ref={input}
          type="file"
          accept=".csv,text/csv"
          className="hidden"
          onChange={(e) => {
            onChange(e.target.files?.[0] ?? null);
            e.target.value = "";
          }}
        />
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
    </div>
  );
}
