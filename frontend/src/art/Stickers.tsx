import { useId } from "react";
import { Rosette } from "./guilloche";
import { FoilSurface } from "./FoilSurface";

export function SecuritySticker({ kind, decorative = false, small = false }: {
  kind: "moex" | "quote" | "coupon"; decorative?: boolean; small?: boolean;
}) {
  const id = useId();
  const title = kind === "moex" ? "Сверено с MOEX" : kind === "quote" ? "Цитата найдена" : "Купон";
  return <span className={`sticker-wrap sticker-${kind}${small ? " sticker-small" : ""}`} aria-hidden={decorative || undefined}>
    <FoilSurface className="security-sticker">
      <svg viewBox="0 0 180 100" preserveAspectRatio="none" aria-hidden="true" className="sticker-foil">
        <defs><linearGradient id={id} x1="0" y1="0" x2="1" y2="1" gradientUnits="objectBoundingBox">
          <stop stopColor="#4FC3F7" /><stop offset=".28" stopColor="#c5bcff" /><stop offset=".52" stopColor="#93e8d8" /><stop offset=".76" stopColor="#f9c6dc" /><stop offset="1" stopColor="#9db5ff" />
        </linearGradient></defs>
        <rect width="180" height="100" rx="16" fill={`url(#${id})`} />
        {Array.from({ length: 13 }, (_, i) => <ellipse key={i} cx="90" cy="50" rx={26+i*7} ry={12+i*5} fill="none" stroke="#1F3A8A" strokeWidth=".6" opacity=".24" transform={`rotate(${i*7} 90 50)`} />)}
      </svg>
      <span className="sticker-symbol" aria-hidden="true">{kind === "moex" ? "✓" : kind === "quote" ? "❞" : "01"}</span>
      <span className="sticker-label">{small ? "С источником" : title}</span>
      {!small && <span className="sticker-micro" aria-hidden="true">{kind === "coupon" ? "Отрывная часть выпуска" : "Документы · данные · проверка"}</span>}
    </FoilSurface>
  </span>;
}

export function FactStamp() {
  return <span className="fact-stamp">Факты, не рекомендация<svg viewBox="0 0 180 54" preserveAspectRatio="none" aria-hidden="true"><rect x="3" y="3" width="174" height="48" rx="24" /><rect x="7" y="7" width="166" height="40" rx="20" /></svg></span>;
}

export function BondComposition() {
  return <div className="bond-composition" aria-hidden="true">
    <FoilSurface className="preview-bond">
      <div className="preview-micro">Документы эмитента · Параметры выпуска · Источники</div>
      <div className="preview-title">Облигация</div>
      <div className="preview-isin">RU · 000 · A10 · CS75</div>
      <Rosette seed="RU000A10CS75" />
      <div className="preview-bottom"><span>Условия выпуска</span><span>№ 000015</span></div>
    </FoilSurface>
    <SecuritySticker kind="moex" decorative />
    <SecuritySticker kind="quote" decorative />
    <SecuritySticker kind="coupon" decorative />
    <span className="composition-caption">У каждого факта — свой источник</span>
  </div>;
}
