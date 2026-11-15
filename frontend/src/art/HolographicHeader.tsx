import { useEffect, useRef } from "react";

// Five moving ink pools and interference lines, drawn locally without textures.
const fragment = `
precision mediump float;
uniform vec2 u_size;
uniform float u_time;
void main() {
  vec2 uv = gl_FragCoord.xy / u_size;
  float t = u_time * .13;
  vec3 col = vec3(.043, .082, .188);
  for (int i = 0; i < 5; i++) {
    float n = float(i);
    vec2 center = vec2(.52 + .38*sin(t*.7+n*1.7), .5+.42*cos(t*.9+n*2.3));
    vec2 d = (uv-center)*vec2(1.3, 1.);
    float pool = exp(-dot(d,d)*7.0);
    vec3 ink = i == 0 ? vec3(.24,.35,1.) : i == 1 ? vec3(.49,.30,1.) :
      i == 2 ? vec3(0.,.75,.65) : i == 3 ? vec3(.31,.76,.97) : vec3(1.,.43,.61);
    col += ink * pool * .19;
  }
  float ripple = sin(uv.x*180.+sin(uv.y*24.+t)*5.+t*3.);
  col += vec3(.13,.18,.25)*pow(.5+.5*ripple,12.)*.13;
  // Dark left edge protects text regardless of the phase of the mesh.
  col *= .56 + .44*smoothstep(.05,.85,uv.x);
  gl_FragColor = vec4(col,1.);
}`;

export function HolographicHeader() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const host = ref.current!;
    const canvas = host.querySelector("canvas")!;
    let gl: WebGLRenderingContext | null = null;
    try { gl = canvas.getContext("webgl", { alpha: false, antialias: false, powerPreference: "low-power" }); }
    catch { /* CSS foil remains visible when WebGL is blocked. */ }
    if (!gl) return;
    const context = gl;
    const shaders: WebGLShader[] = [];
    const program = context.createProgram();
    const buffer = context.createBuffer();
    function dispose() {
      shaders.forEach(s => context.deleteShader(s));
      context.deleteBuffer(buffer);
      context.deleteProgram(program);
    }
    const compile = (type: number, source: string) => {
      const shader = context.createShader(type);
      if (!shader) return null;
      shaders.push(shader);
      context.shaderSource(shader, source);
      context.compileShader(shader);
      return context.getShaderParameter(shader, context.COMPILE_STATUS) ? shader : null;
    };
    const vertex = compile(context.VERTEX_SHADER, "attribute vec2 p; void main(){gl_Position=vec4(p,0.,1.);}");
    const pixel = compile(context.FRAGMENT_SHADER, fragment);
    if (!program || !buffer || !vertex || !pixel) { dispose(); return; }
    context.attachShader(program, vertex);
    context.attachShader(program, pixel);
    context.linkProgram(program);
    if (!context.getProgramParameter(program, context.LINK_STATUS)) { dispose(); return; }
    context.useProgram(program);
    context.bindBuffer(context.ARRAY_BUFFER, buffer);
    context.bufferData(context.ARRAY_BUFFER, new Float32Array([-1,-1, 1,-1, -1,1, -1,1, 1,-1, 1,1]), context.STATIC_DRAW);
    const position = context.getAttribLocation(program, "p");
    context.enableVertexAttribArray(position);
    context.vertexAttribPointer(position, 2, context.FLOAT, false, 0, 0);
    const size = context.getUniformLocation(program, "u_size");
    const time = context.getUniformLocation(program, "u_time");
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let visible = typeof IntersectionObserver === "undefined";
    let lost = false;
    let frame = 0;
    let last = -Infinity;
    let elapsed = 0;
    let previous = 0;
    function draw() {
      context.viewport(0, 0, canvas.width, canvas.height);
      context.uniform2f(size, canvas.width, canvas.height);
      context.uniform1f(time, elapsed / 1000);
      context.drawArrays(context.TRIANGLES, 0, 6);
    }
    function tick(now: number) {
      if (now - last >= 1000 / 30) {
        elapsed += previous ? Math.min(now - previous, 100) : 0;
        previous = now;
        last = now;
        draw();
      }
      frame = requestAnimationFrame(tick);
    }
    function sync() {
      cancelAnimationFrame(frame);
      frame = 0;
      previous = 0;
      const active = visible && !document.hidden && !motion.matches && !lost;
      host.dataset.motion = active ? "active" : "paused";
      if (active) frame = requestAnimationFrame(tick);
      else if (!lost) draw();
    }
    function resize() {
      const rect = host.getBoundingClientRect();
      // Background deliberately undersampled; it contains no text or hard edges.
      const scale = Math.min(window.devicePixelRatio || 1, 1.5) * .7;
      canvas.width = Math.max(1, Math.round(rect.width * scale));
      canvas.height = Math.max(1, Math.round(rect.height * scale));
      if (!lost) draw();
    }
    const intersection = typeof IntersectionObserver !== "undefined" ? new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      sync();
    }) : null;
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(resize) : null;
    function contextLost() {
      lost = true;
      host.dataset.renderer = "fallback";
      sync();
    }
    intersection?.observe(host);
    observer?.observe(host);
    window.addEventListener("resize", resize);
    document.addEventListener("visibilitychange", sync);
    motion.addEventListener("change", sync);
    canvas.addEventListener("webglcontextlost", contextLost);
    host.dataset.renderer = "webgl";
    resize();
    sync();
    return () => {
      cancelAnimationFrame(frame);
      intersection?.disconnect();
      observer?.disconnect();
      window.removeEventListener("resize", resize);
      document.removeEventListener("visibilitychange", sync);
      motion.removeEventListener("change", sync);
      canvas.removeEventListener("webglcontextlost", contextLost);
      dispose();
    };
  }, []);
  return <div ref={ref} className="holographic-header" data-renderer="fallback" data-motion="paused" aria-hidden="true">
    <canvas />
    <svg className="security-mesh" viewBox="0 0 1200 500" preserveAspectRatio="xMidYMid slice" fill="none" stroke="currentColor" strokeWidth=".55">
      {Array.from({ length: 30 }, (_, i) => <path key={i} d={`M-100 ${i*22} C200 ${i*22-160} 280 ${i*22+180} 600 ${i*22} S1000 ${i*22-180} 1300 ${i*22}`} />)}
    </svg>
  </div>;
}
