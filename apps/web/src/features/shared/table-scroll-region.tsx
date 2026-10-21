"use client";

import type { KeyboardEvent, ReactNode } from "react";

const SCROLL_STEP_PX = 96;

export function TableScrollRegion({ label, children }: { label: string; children: ReactNode }) {
  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    event.currentTarget.scrollBy({
      left: event.key === "ArrowRight" ? SCROLL_STEP_PX : -SCROLL_STEP_PX,
      behavior: "smooth",
    });
  }

  return <div className="table-scroll" role="region" aria-label={label} tabIndex={0} onKeyDown={handleKeyDown}>{children}</div>;
}
