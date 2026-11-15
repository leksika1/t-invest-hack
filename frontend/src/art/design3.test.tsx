import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { HolographicHeader } from "./HolographicHeader";
import { FoilSurface } from "./FoilSurface";
import { SecuritySticker, BondComposition, FactStamp } from "./Stickers";
import { CardSummary } from "../CardSummary";
import { card, mismatch } from "../test/fixtures";

let intersect: (entries: { isIntersecting: boolean }[]) => void;
let motion: EventTarget & { matches: boolean };
const disconnect = vi.fn();
beforeEach(() => {
  disconnect.mockClear();
  motion = Object.assign(new EventTarget(), { matches: false });
  vi.stubGlobal("matchMedia", vi.fn(() => motion));
  vi.stubGlobal("IntersectionObserver", class {
    constructor(callback: typeof intersect) { intersect = callback; }
    observe = vi.fn(); disconnect = disconnect;
  });
  vi.stubGlobal("requestAnimationFrame", vi.fn(() => 42));
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
});

function webgl() {
  const gl = Object.fromEntries([
    "shaderSource", "compileShader", "attachShader", "linkProgram", "useProgram", "bindBuffer",
    "bufferData", "enableVertexAttribArray", "vertexAttribPointer", "viewport", "uniform2f", "uniform1f",
    "drawArrays", "deleteShader", "deleteBuffer", "deleteProgram",
  ].map(name => [name, vi.fn()]));
  Object.assign(gl, {
    createShader: vi.fn(() => ({})), createBuffer: vi.fn(() => ({})), createProgram: vi.fn(() => ({})),
    getShaderParameter: vi.fn(() => true), getProgramParameter: vi.fn(() => true),
    getAttribLocation: vi.fn(() => 0), getUniformLocation: vi.fn(() => ({})),
  });
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(gl as unknown as WebGLRenderingContext);
  return gl;
}

it("оставляет статичный доступный fallback, когда WebGL недоступен", () => {
  const { container } = render(<HolographicHeader />);
  expect(container.firstChild).toHaveAttribute("data-renderer", "fallback");
  expect(container.firstChild).toHaveAttribute("aria-hidden", "true");
  expect(requestAnimationFrame).not.toHaveBeenCalled();
});

it("переживает исключение WebGL и ошибку компиляции без цикла кадров", () => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(() => { throw new Error("blocked"); });
  const view = render(<HolographicHeader />);
  expect(view.container.firstChild).toHaveAttribute("data-renderer", "fallback");
  view.unmount();
  const gl = webgl();
  gl.getShaderParameter.mockReturnValue(false);
  const failed = render(<HolographicHeader />);
  expect(failed.container.firstChild).toHaveAttribute("data-renderer", "fallback");
  expect(gl.deleteShader).toHaveBeenCalledTimes(2);
  expect(gl.deleteProgram).toHaveBeenCalled();
  expect(requestAnimationFrame).not.toHaveBeenCalled();
});

it("reduced-motion рисует статичный кадр без RAF, включая смену настройки", () => {
  const gl = webgl();
  motion.matches = true;
  const { container } = render(<HolographicHeader />);
  act(() => intersect([{ isIntersecting: true }]));
  expect(gl.drawArrays).toHaveBeenCalled();
  expect(requestAnimationFrame).not.toHaveBeenCalled();
  expect(container.firstChild).toHaveAttribute("data-motion", "paused");
  act(() => { motion.matches = false; motion.dispatchEvent(new Event("change")); });
  expect(requestAnimationFrame).toHaveBeenCalledTimes(1);
  act(() => { motion.matches = true; motion.dispatchEvent(new Event("change")); });
  expect(cancelAnimationFrame).toHaveBeenCalledWith(42);
  expect(container.firstChild).toHaveAttribute("data-motion", "paused");
});

it("останавливает WebGL вне экрана, в скрытой вкладке и при потере контекста; освобождает ресурсы", () => {
  const gl = webgl();
  const view = render(<HolographicHeader />);
  expect(requestAnimationFrame).not.toHaveBeenCalled();
  act(() => intersect([{ isIntersecting: true }]));
  expect(view.container.firstChild).toHaveAttribute("data-motion", "active");
  act(() => intersect([{ isIntersecting: false }]));
  expect(view.container.firstChild).toHaveAttribute("data-motion", "paused");
  act(() => intersect([{ isIntersecting: true }]));
  vi.spyOn(document, "hidden", "get").mockReturnValue(true);
  fireEvent(document, new Event("visibilitychange"));
  expect(view.container.firstChild).toHaveAttribute("data-motion", "paused");
  fireEvent(view.container.querySelector("canvas")!, new Event("webglcontextlost"));
  expect(view.container.firstChild).toHaveAttribute("data-renderer", "fallback");
  view.unmount();
  expect(gl.deleteBuffer).toHaveBeenCalled();
  expect(gl.deleteShader).toHaveBeenCalledTimes(2);
  expect(disconnect).toHaveBeenCalled();
});

it("бланк и фольга соблюдают видимость и reduced-motion", () => {
  const { container } = render(<FoilSurface as="section" label="Бланк">Условия</FoilSurface>);
  act(() => intersect([{ isIntersecting: true }]));
  expect(container.firstChild).toHaveAttribute("data-motion", "active");
  act(() => { motion.matches = true; motion.dispatchEvent(new Event("change")); });
  expect(container.firstChild).toHaveAttribute("data-motion", "paused");
  expect(screen.getByRole("region", { name: "Бланк" })).toBeVisible();
});

it("ограничивает частоту отрисовки и разрешение даже на Retina", () => {
  const gl = webgl();
  vi.stubGlobal("devicePixelRatio", 3);
  vi.spyOn(HTMLDivElement.prototype, "getBoundingClientRect").mockReturnValue({ width: 1000, height: 400 } as DOMRect);
  const { container } = render(<HolographicHeader />);
  const canvas = container.querySelector("canvas")!;
  expect(canvas.width).toBeLessThanOrEqual(1500);
  expect(canvas.height).toBeLessThanOrEqual(600);
  act(() => intersect([{ isIntersecting: true }]));
  const callback = vi.mocked(requestAnimationFrame).mock.calls.at(-1)![0];
  const initialDraws = gl.drawArrays.mock.calls.length;
  act(() => { callback(100); callback(110); callback(120); callback(134); });
  expect(gl.drawArrays.mock.calls.length - initialDraws).toBe(2);
});

it("блик следует за мышью по двум осям без inline-стилей и замирает при reduced-motion", () => {
  vi.stubGlobal("PointerEvent", MouseEvent);
  const { container } = render(<FoilSurface>Бланк</FoilSurface>);
  const surface = container.firstElementChild!;
  vi.spyOn(surface, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 100, height: 90 } as DOMRect);
  fireEvent.pointerMove(surface, { clientX: 10, clientY: 10 });
  expect(surface).toHaveAttribute("data-foil", "0-0");
  fireEvent.pointerMove(surface, { clientX: 90, clientY: 80 });
  expect(surface).toHaveAttribute("data-foil", "2-2");
  expect(surface).not.toHaveAttribute("style");
  motion.matches = true;
  fireEvent.pointerMove(surface, { clientX: 10, clientY: 10 });
  expect(surface).toHaveAttribute("data-foil", "2-2");
  fireEvent.pointerLeave(surface);
  expect(surface).not.toHaveAttribute("data-foil");
});

it("смысловые стикеры имеют текст; композиция и декоративные стикеры скрыты от скринридера", () => {
  const view = render(<><SecuritySticker kind="moex" /><SecuritySticker kind="quote" small /><FactStamp /></>);
  expect(screen.getByText("Сверено с MOEX")).toBeVisible();
  expect(screen.getByText("С источником")).toBeVisible();
  expect(screen.getByText("Факты, не рекомендация")).toBeVisible();
  expect(view.container.querySelectorAll("svg:not([aria-hidden='true'])")).toHaveLength(0);
  view.rerender(<><BondComposition /><SecuritySticker kind="coupon" decorative /></>);
  expect(view.container.querySelector(".bond-composition")).toHaveAttribute("aria-hidden", "true");
  expect(view.container.querySelector(".sticker-coupon")).toHaveAttribute("aria-hidden", "true");
});

it("сводка не заявляет сверку или найденную цитату до наличия подтверждённых данных", () => {
  const view = render(<CardSummary card={{ ...card, iss_fields: { face_value: mismatch }, risk_fields: {} }} />);
  expect(screen.queryByText("Сверено с MOEX")).not.toBeInTheDocument();
  expect(screen.queryByText("Цитата найдена")).not.toBeInTheDocument();
  view.rerender(<CardSummary card={{ ...card,
    iss_fields: { face_value: { ...mismatch, status: "verified_iss" } },
    risk_fields: { issuer_call_option: { ...mismatch, code: "issuer_call_option", status: "extracted" } },
  }} />);
  expect(screen.getByText("Сверено с MOEX")).toBeVisible();
  expect(screen.getByText("Цитата найдена")).toBeVisible();
});
