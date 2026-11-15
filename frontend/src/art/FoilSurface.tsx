import { useEffect, useRef } from "react";
import type { ReactNode, PointerEvent } from "react";

export function FoilSurface({ children, className = "", as: Tag = "div", label }: {
  children: ReactNode; className?: string; as?: "div" | "section"; label?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = ref.current!;
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let visible = typeof IntersectionObserver === "undefined";
    const sync = () => { element.dataset.motion = visible && !document.hidden && !motion.matches ? "active" : "paused"; };
    const observer = typeof IntersectionObserver === "undefined" ? null : new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      sync();
    });
    observer?.observe(element);
    motion.addEventListener("change", sync);
    document.addEventListener("visibilitychange", sync);
    sync();
    return () => {
      observer?.disconnect();
      motion.removeEventListener("change", sync);
      document.removeEventListener("visibilitychange", sync);
    };
  }, []);
  function tilt(event: PointerEvent<HTMLDivElement>) {
    if (event.pointerType === "touch" || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const column = Math.max(0, Math.min(2, Math.floor((event.clientX - rect.left) / Math.max(1, rect.width) * 3)));
    const row = Math.max(0, Math.min(2, Math.floor((event.clientY - rect.top) / Math.max(1, rect.height) * 3)));
    // Attribute positions keep cursor tracking compatible with style-src 'self'.
    event.currentTarget.dataset.foil = `${column}-${row}`;
  }
  return <Tag ref={ref} className={`foil-surface ${className}`} aria-label={label} data-motion="paused" onPointerMove={tilt} onPointerLeave={e => { delete e.currentTarget.dataset.foil; }}>
    {children}
  </Tag>;
}
