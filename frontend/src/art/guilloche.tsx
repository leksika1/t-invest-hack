// Pure, deterministic parametric curves. No IDs, random state or external assets.
export function seedNumber(seed: string) {
  return Array.from(seed).reduce(
    (n, c) => Math.imul(n ^ c.charCodeAt(0), 16777619) >>> 0,
    2166136261,
  );
}
export function rosettePaths(seed: string, layers = 5): string[] {
  const n = seedNumber(seed);
  return Array.from({ length: layers }, (_, layer) => {
    const lobes = [17, 19, 23, 25, 27, 29][n % 6];
    const radius = 70 + layer * 3;
    const amplitude = 23 + layer * 1.5;
    const phase = ((n % 360) * Math.PI) / 180 + layer * 0.11;
    return (
      Array.from({ length: 1441 }, (_, i) => {
        // A hypotrochoid closes after seven revolutions; the inner wheel
        // crosses the outer curve to form interwoven security-paper loops.
        const t = (i / 1440) * Math.PI * 14;
        const x =
          120 +
          radius * Math.cos(t) +
          amplitude * Math.cos((lobes / 7) * t + phase);
        const y =
          120 +
          radius * Math.sin(t) -
          amplitude * Math.sin((lobes / 7) * t + phase);
        return `${i ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)}`;
      }).join(" ") + " Z"
    );
  });
}
export function Rosette({
  seed = "bond-paper",
  className = "",
}: {
  seed?: string;
  className?: string;
}) {
  return (
    <svg
      className={`rosette ${className}`}
      viewBox="0 0 240 240"
      fill="none"
      stroke="currentColor"
      strokeWidth=".65"
      aria-hidden="true"
    >
      {rosettePaths(seed).map((d, i) => (
        <path d={d} key={i} pathLength="1" />
      ))}
      <circle cx="120" cy="120" r="32" />
      <circle cx="120" cy="120" r="35" />
    </svg>
  );
}
export function GuillocheBorder({ seed }: { seed: string }) {
  const phase = seedNumber(seed) % 20;
  return (
    <svg
      className="guilloche-border"
      preserveAspectRatio="none"
      viewBox="0 0 800 400"
      fill="none"
      stroke="currentColor"
      strokeWidth=".6"
      aria-hidden="true"
    >
      {[0, 1, 2].map((layer) => (
        <path
          key={layer}
          d={Array.from(
            { length: 801 },
            (_, i) =>
              `${i ? "L" : "M"}${i},${5 + layer * 2 + Math.sin((i + phase) / 5) * 2}`,
          ).join(" ")}
        />
      ))}
      <rect x="2" y="2" width="796" height="396" />
      <rect x="9" y="11" width="782" height="380" />
    </svg>
  );
}
export function PaperIllustration({
  kind = "documents",
}: {
  kind?: "documents" | "upload" | "model";
}) {
  return (
    <svg
      className="paper-illustration"
      viewBox="0 0 80 80"
      fill="none"
      stroke="currentColor"
      strokeWidth="1"
      aria-hidden="true"
    >
      {kind === "model" ? (
        <>
          <rect x="15" y="20" width="50" height="40" rx="3" />
          <path d="M25 32h30M25 40h20M25 48h26M30 14v6m20-6v6M30 60v6m20-6v6" />
        </>
      ) : (
        <>
          {kind === "documents" && <><path d="M18 24H12v49h42v-6M24 18h-6v49h42v-6" /><path d="M31 10v15l5-4 5 4V10" fill="currentColor" opacity=".22" /></>}
          <path d="M25 10h28l14 14v37H25zM53 10v14h14M33 32h24M33 37h18" />
          {kind === "upload" ? (
            <path d="M40 54V42m-5 5 5-5 5 5" />
          ) : (
            <><circle cx="47" cy="49" r="11" fill="var(--surface)" /><path d="m55 57 12 12M41 45h11M41 49h8" /></>
          )}
        </>
      )}
    </svg>
  );
}
