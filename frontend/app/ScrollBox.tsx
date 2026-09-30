"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

// A list that scrolls inside its own box instead of stretching the page. The top/bottom
// edge fades only while there is more content that way, so it's clear the box scrolls.
export default function ScrollBox({
  children,
  size = "default",
  label,
}: {
  children: ReactNode;
  size?: "default" | "short" | "tall";
  label?: string;
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  const innerRef = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ top: false, bottom: false });

  useEffect(() => {
    const box = boxRef.current;
    const inner = innerRef.current;
    if (!box || !inner) return;
    const update = () =>
      setEdges({
        top: box.scrollTop > 4,
        bottom: box.scrollHeight - box.scrollTop - box.clientHeight > 4,
      });
    update();
    box.addEventListener("scroll", update, { passive: true });
    // The box itself stops growing at its max height, so watch the content too
    // (it changes when a refresh brings in new interviews or themes).
    const observer = new ResizeObserver(update);
    observer.observe(box);
    observer.observe(inner);
    return () => {
      box.removeEventListener("scroll", update);
      observer.disconnect();
    };
  }, []);

  const scrollable = edges.top || edges.bottom;
  const classes = [
    "scroll-box",
    size !== "default" && size,
    edges.top && "fade-top",
    edges.bottom && "fade-bottom",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div
      ref={boxRef}
      className={classes}
      // Keyboard users can scroll it once it actually scrolls.
      tabIndex={scrollable ? 0 : undefined}
      role={scrollable ? "region" : undefined}
      aria-label={scrollable ? label : undefined}
    >
      <div ref={innerRef}>{children}</div>
    </div>
  );
}
