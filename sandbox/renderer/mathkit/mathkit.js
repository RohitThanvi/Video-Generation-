/*
 * mathkit - a small manim-style toolkit for 3Blue1Brown-like explainer videos.
 *
 *   import { Scene, COLORS, create, write, fadeIn, fadeOut, morph } from 'mathkit';
 *
 * The frame is 16 x 9 "frame units" (like manim): x from -8 to 8, y from -4.5 to 4.5, y up,
 * origin at the centre. One frame unit = 120 px at 1920x1080.
 *
 * Elements are INVISIBLE until introduced: scene.add(el) shows them from the current time,
 * or an animation (create / fadeIn / write / ...) reveals them. Animations are played one
 * after another with scene.play(...); animations passed to the same play() run together.
 *
 * The scene builds a paused GSAP timeline and exposes it to the renderer through
 * window.__renderFrame, so rendering is frame-exact. Call scene.start() last.
 */
import gsap from 'gsap';
import katex from 'katex';

export const COLORS = {
  BLACK: '#000000', WHITE: '#FFFFFF', GREY: '#888888', LIGHT_GREY: '#BBBBBB', DARK_GREY: '#444444',
  BLUE: '#58C4DD', BLUE_D: '#29ABCA', BLUE_E: '#1C758A', TEAL: '#5CD0B3', GREEN: '#83C167',
  YELLOW: '#FFFF00', GOLD: '#F0AC5F', ORANGE: '#FF862F', RED: '#FC6255', MAROON: '#C55F73',
  PURPLE: '#9A72AC', PINK: '#D147BD',
};
export const SMOOTH = 'power2.inOut';

const W = 1920;
const H = 1080;
export const UNIT = 120; // pixels per frame unit
export const FRAME = { width: W / UNIT, height: H / UNIT };

const SVG_NS = 'http://www.w3.org/2000/svg';
const XHTML_NS = 'http://www.w3.org/1999/xhtml';
const SHAPES = 'path,circle,ellipse,rect,polygon,polyline,line';
const FONT_SPECS = [
  '1em KaTeX_Main', 'bold 1em KaTeX_Main', 'italic 1em KaTeX_Main', 'italic 1em KaTeX_Math',
  '1em KaTeX_AMS', '1em KaTeX_Size1', '1em KaTeX_Size2', '1em KaTeX_Size3', '1em KaTeX_Size4',
];

// ---------------------------------------------------------------- small utilities

export const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
export const lerp = (a, b, u) => a + (b - a) * u;
export const linspace = (a, b, n) => Array.from({ length: n }, (_, i) => a + ((b - a) * i) / (n - 1));
/** Seeded random numbers in [0,1). Math.random() would make renders differ between runs. */
export function rng(seed = 1) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const px = (x, y) => [W / 2 + x * UNIT, H / 2 - y * UNIT];
const num = (v) => +v.toFixed(2);

function svgEl(tag, attrs = {}, parent) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

const isPaint = (value) => value != null && value !== 'none';
const shapesOf = (target) => (target.matches && target.matches(SHAPES) ? [target] : [...target.querySelectorAll(SHAPES)]);
const textsOf = (target) => (target.tagName === 'foreignObject' ? [target] : [...target.querySelectorAll('foreignObject')]);

function tickValues(a, b, step) {
  const out = [];
  for (let v = Math.ceil(a / step - 1e-9) * step; v <= b + 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}

/** SVG path data for a list of pixel points; NaN / off-screen values break the line. */
function curveD(points) {
  let d = '';
  let pen = false;
  for (const [x, y] of points) {
    if (!Number.isFinite(x) || !Number.isFinite(y) || Math.abs(y) > 4000 || Math.abs(x) > 4000) {
      pen = false;
      continue;
    }
    d += `${pen ? 'L' : 'M'}${num(x)} ${num(y)}`;
    pen = true;
  }
  return d || 'M0 0';
}

// ---------------------------------------------------------------- animations

export class Anim {
  constructor(duration, build) {
    this.duration = duration;
    this.build = build; // (timeline, startTime) => void
  }
}

/** Draw an element's outline (and fade in its fill / text). */
export function create(target, { duration = 1, ease = SMOOTH } = {}) {
  return new Anim(duration, (tl, at) => {
    tl.set(target, { opacity: 1 }, at);
    for (const shape of shapesOf(target)) {
      const hasStroke = isPaint(shape.getAttribute('stroke'));
      const hasFill = isPaint(shape.getAttribute('fill'));
      const fillOpacity = shape.getAttribute('fill-opacity') ?? 1;
      if (hasStroke && !shape.__noDraw) {
        // Attribute tweens, not CSS: GSAP rounds px-valued CSS properties (stroke-dashoffset,
        // stroke-width) to whole pixels, which would make the drawing jump instead of glide.
        tl.fromTo(shape, { attr: { 'stroke-dasharray': 1, 'stroke-dashoffset': 1 } }, { attr: { 'stroke-dashoffset': 0 }, duration, ease }, at);
        tl.set(shape, { attr: { 'stroke-dasharray': shape.__dash || 'none' } }, at + duration);
      } else if (hasStroke) {
        tl.fromTo(shape, { strokeOpacity: 0 }, { strokeOpacity: 1, duration, ease }, at);
      }
      if (hasFill) {
        const lag = hasStroke ? duration / 2 : 0;
        tl.fromTo(shape, { fillOpacity: 0 }, { fillOpacity: fillOpacity, duration: duration - lag, ease }, at + lag);
      }
    }
    for (const text of textsOf(target)) {
      tl.fromTo(text, { opacity: 0 }, { opacity: 1, duration: duration / 2, ease }, at + duration / 2);
    }
  });
}

/** Reverse of create. */
export function uncreate(target, { duration = 1, ease = SMOOTH } = {}) {
  return new Anim(duration, (tl, at) => {
    for (const shape of shapesOf(target)) {
      if (isPaint(shape.getAttribute('stroke')) && !shape.__noDraw) {
        tl.set(shape, { attr: { 'stroke-dasharray': 1 } }, at);
        tl.to(shape, { attr: { 'stroke-dashoffset': 1 }, duration, ease }, at);
      }
    }
    tl.to(target, { opacity: 0, duration: duration / 4, ease: 'none' }, at + duration * 0.75);
  });
}

export function fadeIn(target, { duration = 0.8, shift = [0, 0], scale = 1, ease = SMOOTH } = {}) {
  return new Anim(duration, (tl, at) => {
    const from = { opacity: 0 };
    const to = { opacity: 1, duration, ease };
    if (shift[0] || shift[1]) {
      Object.assign(from, { x: shift[0] * UNIT, y: -shift[1] * UNIT });
      Object.assign(to, { x: 0, y: 0 });
    }
    if (scale !== 1) {
      Object.assign(from, { scale, transformOrigin: '50% 50%' });
      Object.assign(to, { scale: 1 });
    }
    tl.fromTo(target, from, to, at);
  });
}

export function fadeOut(target, { duration = 0.8, shift = [0, 0], ease = SMOOTH } = {}) {
  return new Anim(duration, (tl, at) => {
    const to = { opacity: 0, duration, ease };
    if (shift[0] || shift[1]) Object.assign(to, { x: `+=${shift[0] * UNIT}`, y: `+=${-shift[1] * UNIT}` });
    tl.to(target, to, at);
  });
}

/** Reveal text / LaTeX from left to right (and create any shapes in the target). */
export function write(target, { duration = 1.2, ease = 'none' } = {}) {
  return new Anim(duration, (tl, at) => {
    tl.set(target, { opacity: 1 }, at);
    for (const text of textsOf(target)) {
      const content = text.__content;
      tl.set(text, { opacity: 1 }, at);
      tl.fromTo(
        content,
        { clipPath: 'inset(-25% 100% -25% 0%)' },
        { clipPath: 'inset(-25% -3% -25% 0%)', duration, ease },
        at,
      );
    }
    if (shapesOf(target).length) create(target, { duration })?.build(tl, at);
  });
}

/** Move by (dx, dy) frame units (y up). */
export function shift(target, [dx, dy], { duration = 1, ease = SMOOTH } = {}) {
  return new Anim(duration, (tl, at) => {
    tl.to(target, { x: `+=${dx * UNIT}`, y: `+=${-dy * UNIT}`, duration, ease }, at);
  });
}

/** Move text / dots / circles to an absolute frame position. */
export function moveTo(target, [x, y], { duration = 1, ease = SMOOTH } = {}) {
  return new Anim(duration, (tl, at) => {
    if (!target.__anchor) throw new Error('moveTo works on text, tex, dot and circle elements; use shift() for others.');
    const [tx, ty] = px(x, y);
    const [ax, ay] = target.__anchor;
    tl.to(target, { x: `+=${tx - ax}`, y: `+=${ty - ay}`, duration, ease }, at);
    target.__anchor = [tx, ty];
  });
}

export function scaleTo(target, factor, { duration = 1, ease = SMOOTH, about } = {}) {
  return new Anim(duration, (tl, at) => {
    const origin = about ? px(...about) : target.__anchor;
    const vars = { scale: factor, duration, ease };
    if (origin) vars.svgOrigin = `${origin[0]} ${origin[1]}`;
    else vars.transformOrigin = '50% 50%';
    tl.to(target, vars, at);
  });
}

export function rotate(target, degrees, { duration = 1, ease = SMOOTH, about } = {}) {
  return new Anim(duration, (tl, at) => {
    const origin = about ? px(...about) : target.__anchor;
    const vars = { rotation: `+=${-degrees}`, duration, ease };
    if (origin) vars.svgOrigin = `${origin[0]} ${origin[1]}`;
    else vars.transformOrigin = '50% 50%';
    tl.to(target, vars, at);
  });
}

export function colorTo(target, color, { duration = 0.8, ease = SMOOTH } = {}) {
  return new Anim(duration, (tl, at) => {
    for (const shape of shapesOf(target)) {
      const vars = { duration, ease };
      if (isPaint(shape.getAttribute('stroke'))) vars.stroke = color;
      if (isPaint(shape.getAttribute('fill'))) vars.fill = color;
      tl.to(shape, vars, at);
    }
    for (const text of textsOf(target)) tl.to(text.__content, { color, duration, ease }, at);
  });
}

/** Cross-fade one element into another that sits in the same place. */
export function swap(from, to, { duration = 0.8, ease = SMOOTH } = {}) {
  return new Anim(duration, (tl, at) => {
    tl.to(from, { opacity: 0, duration: duration * 0.45, ease: 'power1.in' }, at);
    tl.fromTo(to, { opacity: 0 }, { opacity: 1, duration: duration * 0.45, ease: 'power1.out' }, at + duration * 0.55);
  });
}

/**
 * Call fn(u) with u going 0 -> 1 over `duration`. The escape hatch for anything custom,
 * e.g. counters, moving points, updating shapes.
 */
export function custom(duration, fn, { ease = 'none' } = {}) {
  return new Anim(duration, (tl, at) => {
    const state = { u: 0 };
    // immediateRender:false - otherwise building a later tween would apply its start state
    // right now and clobber the state of earlier ones.
    tl.fromTo(state, { u: 0 }, { u: 1, duration, ease, immediateRender: false, onUpdate: () => fn(state.u) }, at);
  });
}

/** Morph a plot made with axes.plot() into another function (3b1b "Transform"). */
export function morph(path, fn, { duration = 1.5, ease = SMOOTH } = {}) {
  return new Anim(duration, (tl, at) => {
    const plot = path.__plot;
    if (!plot) throw new Error('morph() needs a path created by axes.plot().');
    const from = plot.ys.slice();
    const to = plot.xs.map((x) => fn(x));
    plot.ys = to;
    plot.fn = fn;
    tl.fromTo(
      { u: 0 },
      { u: 0 },
      {
        u: 1,
        duration,
        ease,
        immediateRender: false,
        onUpdate() {
          const u = this.targets()[0].u;
          path.setAttribute('d', curveD(plot.xs.map((x, i) => plot.axes.c2p(x, lerp(from[i], to[i], u)))));
        },
      },
      at,
    );
  });
}

/** Move a dot along a path: f(u) returns the frame position [x, y] for u in 0..1. */
export function along(dot, f, { duration = 2, ease = SMOOTH } = {}) {
  return custom(duration, (u) => {
    const [x, y] = px(...f(u));
    dot.setAttribute('cx', num(x));
    dot.setAttribute('cy', num(y));
    dot.__anchor = [x, y];
  }, { ease });
}

// ---------------------------------------------------------------- scene

export class Scene {
  constructor({ background = COLORS.BLACK } = {}) {
    document.documentElement.style.background = background;
    Object.assign(document.body.style, { margin: '0', overflow: 'hidden', background });
    this.svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'xMidYMid meet' }, document.body);
    Object.assign(this.svg.style, { position: 'fixed', inset: '0', width: '100vw', height: '100vh', display: 'block' });
    this.layer = svgEl('g', {}, this.svg);
    this.tl = gsap.timeline({ paused: true });
    this.cursor = 0;
    this.cam = { cx: 0, cy: 0, scale: 1 };
    this.started = false;

    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/vendor/katex/dist/katex.min.css';
    document.head.appendChild(link);
    const cssLoaded = new Promise((resolve) => {
      link.onload = resolve;
      link.onerror = resolve;
    });
    this.ready = cssLoaded.then(() => Promise.all(FONT_SPECS.map((f) => document.fonts.load(f).catch(() => {}))));
    // The renderer waits for this before capturing the first frame.
    window.__VIDEO_READY = Promise.all([window.__VIDEO_READY, this.ready]);
  }

  // ---- timeline ----

  /** Play animations together, then move on once the longest has finished. */
  play(...anims) {
    this._autostart();
    const list = anims.flat().filter(Boolean);
    let longest = 0;
    for (const anim of list) {
      anim.build(this.tl, this.cursor);
      longest = Math.max(longest, anim.duration);
    }
    this.cursor += longest;
    return this;
  }

  wait(seconds = 1) {
    this._autostart();
    this.cursor += seconds;
    return this;
  }

  /** Hold the last frame until `seconds` have passed (use window.__VIDEO_TOTAL__). */
  padTo(seconds) {
    if (seconds > this.cursor) this.cursor = seconds;
    return this;
  }

  /** Show elements instantly, from the current time on. */
  add(...els) {
    for (const el of els.flat()) {
      if (this.cursor === 0) el.style.opacity = '1';
      this.tl.set(el, { opacity: 1 }, this.cursor);
    }
    return this;
  }

  remove(...els) {
    for (const el of els.flat()) this.tl.set(el, { opacity: 0 }, this.cursor);
    return this;
  }

  /** Pan / zoom the whole scene. */
  camera({ center = [0, 0], zoom = 1, duration = 1.5, ease = SMOOTH } = {}) {
    const from = { ...this.cam };
    const to = { cx: center[0], cy: center[1], scale: zoom };
    this.cam = to;
    const apply = (c) => {
      const [x, y] = px(c.cx, c.cy);
      this.layer.setAttribute('transform', `translate(${W / 2} ${H / 2}) scale(${c.scale}) translate(${-x} ${-y})`);
    };
    return new Anim(duration, (tl, at) => {
      tl.fromTo(
        { u: 0 },
        { u: 0 },
        {
          u: 1,
          duration,
          ease,
          immediateRender: false,
          onUpdate() {
            const u = this.targets()[0].u;
            apply({ cx: lerp(from.cx, to.cx, u), cy: lerp(from.cy, to.cy, u), scale: lerp(from.scale, to.scale, u) });
          },
        },
        at,
      );
    });
  }

  _autostart() {
    if (this._scheduled) return;
    this._scheduled = true;
    setTimeout(() => this.start(), 0);
  }

  /** Finish building. Call once, after the last play()/wait(). */
  start() {
    if (this.started) return this;
    this.started = true;
    this.duration = this.cursor;
    const total = window.__VIDEO_TOTAL__;
    if (window.__VIDEO_EXPORT__ && total && Math.abs(total - this.cursor) > 0.1) {
      console.error(
        `mathkit: the animation lasts ${this.cursor.toFixed(2)}s but the storyboard totals ${total.toFixed(2)}s. ` +
          'Make them equal (scene.padTo(window.__VIDEO_TOTAL__) holds the last frame).',
      );
    }
    if (window.__VIDEO_EXPORT__) {
      window.__renderFrame = (tMs) => {
        this.tl.time(Math.min(tMs / 1000, this.tl.duration()), false);
      };
    } else {
      this.tl.play();
    }
    return this;
  }

  // ---- shapes (frame coordinates, y up) ----

  _shape(tag, attrs, o = {}) {
    const hasFill = o.fill != null && o.fill !== 'none';
    const stroke = o.stroke === false ? 'none' : o.color || (hasFill ? o.fill : COLORS.WHITE);
    const node = svgEl(tag, {
      ...attrs,
      stroke,
      'stroke-width': o.width ?? 4,
      fill: hasFill ? o.fill : 'none',
      'fill-opacity': o.fillOpacity ?? 1,
      'stroke-linecap': 'round',
      'stroke-linejoin': 'round',
    }, this.layer);
    if (o.dashed) {
      node.setAttribute('stroke-dasharray', '16 12');
      node.__dash = '16 12';
      node.__noDraw = true; // pathLength normalisation would rescale the dashes
    } else {
      node.setAttribute('pathLength', '1');
    }
    node.style.opacity = '0';
    return node;
  }

  line(a, b, o = {}) {
    const [x1, y1] = px(...a);
    const [x2, y2] = px(...b);
    return this._shape('path', { d: `M${num(x1)} ${num(y1)}L${num(x2)} ${num(y2)}` }, o);
  }

  polyline(points, o = {}) {
    return this._shape('path', { d: curveD(points.map((p) => px(...p))) }, o);
  }

  polygon(points, o = {}) {
    return this._shape('path', { d: curveD(points.map((p) => px(...p))) + 'Z' }, o);
  }

  rect(center, width, height, o = {}) {
    const [cx, cy] = center;
    const hw = width / 2;
    const hh = height / 2;
    return this.polygon([[cx - hw, cy - hh], [cx + hw, cy - hh], [cx + hw, cy + hh], [cx - hw, cy + hh]], o);
  }

  circle(center, radius, o = {}) {
    const [cx, cy] = px(...center);
    const node = this._shape('circle', { cx: num(cx), cy: num(cy), r: num(radius * UNIT) }, o);
    node.__anchor = [cx, cy];
    return node;
  }

  dot(center, o = {}) {
    const node = this.circle(center, o.radius ?? 0.09, { fill: o.color || COLORS.WHITE, stroke: false, ...o });
    return node;
  }

  /** Circular arc from angle a0 to a1 (radians, counter-clockwise). */
  arc(center, radius, a0, a1, o = {}) {
    const pts = linspace(a0, a1, 64).map((a) => [center[0] + radius * Math.cos(a), center[1] + radius * Math.sin(a)]);
    return this.polyline(pts, o);
  }

  /** Curve from a function t -> [x, y] in frame coordinates. */
  parametric(fn, { tRange = [0, 1], samples = 300, ...o } = {}) {
    return this.polyline(linspace(tRange[0], tRange[1], samples).map((t) => fn(t)), o);
  }

  arrow(a, b, o = {}) {
    const g = svgEl('g', {}, this.layer);
    g.style.opacity = '0';
    const color = o.color || COLORS.WHITE;
    const tip = o.tip ?? 0.22;
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const len = Math.hypot(dx, dy) || 1;
    const ux = dx / len;
    const uy = dy / len;
    const base = [b[0] - ux * tip, b[1] - uy * tip];
    const shaft = this.line(a, base, { color, width: o.width ?? 4 });
    const head = this.polygon(
      [b, [base[0] - uy * tip * 0.45, base[1] + ux * tip * 0.45], [base[0] + uy * tip * 0.45, base[1] - ux * tip * 0.45]],
      { fill: color, color, width: 1 },
    );
    for (const part of [shaft, head]) {
      part.style.opacity = '1';
      g.appendChild(part);
    }
    return g;
  }

  /** Group elements so they can be animated together. */
  group(...els) {
    const g = svgEl('g', {}, this.layer);
    g.style.opacity = '0';
    for (const el of els.flat()) {
      el.style.opacity = '1';
      g.appendChild(el);
    }
    return g;
  }

  // ---- text ----

  _text(inner, { at = [0, 0], size = 56, color = COLORS.WHITE, anchor = 'center', font = 'serif' } = {}, parent = this.layer) {
    const [cx, cy] = px(...at);
    const boxW = 1800;
    const boxH = 420;
    const x = anchor === 'left' ? cx : anchor === 'right' ? cx - boxW : cx - boxW / 2;
    const fo = svgEl('foreignObject', { x: num(x), y: num(cy - boxH / 2), width: boxW, height: boxH }, parent);
    fo.style.overflow = 'visible';
    const justify = anchor === 'left' ? 'flex-start' : anchor === 'right' ? 'flex-end' : 'center';
    const row = document.createElementNS(XHTML_NS, 'div');
    row.setAttribute('style', `display:flex;align-items:center;justify-content:${justify};width:100%;height:100%`);
    const content = document.createElementNS(XHTML_NS, 'div');
    const family = font === 'sans' ? "'DejaVu Sans', Arial, sans-serif" : "KaTeX_Main, 'DejaVu Serif', serif";
    content.setAttribute(
      'style',
      `font-size:${size}px;color:${color};white-space:nowrap;line-height:1.25;text-align:center;font-family:${family}`,
    );
    content.innerHTML = inner;
    row.appendChild(content);
    fo.appendChild(row);
    fo.__content = content;
    fo.__anchor = [cx, cy];
    fo.style.opacity = '0';
    return fo;
  }

  /** Plain text. Use "\n" for new lines. */
  text(str, o = {}) {
    const safe = String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\n/g, '<br>');
    return this._text(safe, o);
  }

  /** LaTeX via KaTeX, e.g. scene.tex('e^{i\\pi}+1=0', { at: [0, 2], color: COLORS.YELLOW }). */
  tex(latex, o = {}) {
    const html = katex.renderToString(latex, { throwOnError: false, output: 'html', displayMode: !!o.display });
    return this._text(html, o);
  }

  // ---- coordinate system ----

  /**
   * Axes (optionally a full number plane with grid: true).
   * xRange / yRange are [min, max, step]. By default the axes are centred in the frame; pass
   * center: [x, y] to move them, or xLength / yLength (frame units) to scale them.
   * Returns a group with helpers: c2p(x, y) -> frame position, plot(fn), point(x, y),
   * vector([x, y]), area(fn, [a, b]).
   */
  axes(o = {}) {
    const {
      xRange = [-4, 4, 1], yRange = [-3, 3, 1], color = COLORS.GREY, width = 3, labels = false,
      grid = false, arrows = true, center = [0, 0], xLabel, yLabel, labelSize = 26,
    } = o;
    const [x0, x1, xStep] = xRange;
    const [y0, y1, yStep] = yRange;
    const xUnit = o.xLength ? o.xLength / (x1 - x0) : 1;
    const yUnit = o.yLength ? o.yLength / (y1 - y0) : 1;
    const ox = o.origin ? o.origin[0] : center[0] - ((x0 + x1) / 2) * xUnit;
    const oy = o.origin ? o.origin[1] : center[1] - ((y0 + y1) / 2) * yUnit;
    const c2f = (cx, cy) => [ox + cx * xUnit, oy + cy * yUnit];
    const c2p = c2f; // frame coordinates are what every builder accepts
    const axisY = clamp(0, y0, y1);
    const axisX = clamp(0, x0, x1);

    const g = svgEl('g', {}, this.layer);
    g.style.opacity = '0';
    const part = (node) => {
      node.style.opacity = '1';
      g.appendChild(node);
      return node;
    };

    if (grid) {
      for (const v of tickValues(x0, x1, xStep)) part(this.line(c2f(v, y0), c2f(v, y1), { color: COLORS.BLUE_E, width: 2 })).setAttribute('stroke-opacity', '0.45');
      for (const v of tickValues(y0, y1, yStep)) part(this.line(c2f(x0, v), c2f(x1, v), { color: COLORS.BLUE_E, width: 2 })).setAttribute('stroke-opacity', '0.45');
    }
    part(this.line(c2f(x0, axisY), c2f(x1, axisY), { color, width }));
    part(this.line(c2f(axisX, y0), c2f(axisX, y1), { color, width }));
    if (arrows) {
      const tip = 0.16;
      part(this.polygon([c2f(x1, axisY), [c2f(x1, axisY)[0] - tip, oy + axisY * yUnit + tip * 0.45], [c2f(x1, axisY)[0] - tip, oy + axisY * yUnit - tip * 0.45]], { fill: color, color, width: 1 }));
      part(this.polygon([c2f(axisX, y1), [ox + axisX * xUnit - tip * 0.45, c2f(axisX, y1)[1] - tip], [ox + axisX * xUnit + tip * 0.45, c2f(axisX, y1)[1] - tip]], { fill: color, color, width: 1 }));
    }
    const tickLen = 0.08;
    for (const v of tickValues(x0, x1, xStep)) {
      if (Math.abs(v - axisX) < 1e-9) continue;
      const [fx, fy] = c2f(v, axisY);
      part(this.line([fx, fy - tickLen], [fx, fy + tickLen], { color, width }));
      if (labels) this._text(katex.renderToString(String(+v.toFixed(6)), { output: 'html' }), { at: [fx, fy - 0.3], size: labelSize, color }, g).style.opacity = '1';
    }
    for (const v of tickValues(y0, y1, yStep)) {
      if (Math.abs(v - axisY) < 1e-9) continue;
      const [fx, fy] = c2f(axisX, v);
      part(this.line([fx - tickLen, fy], [fx + tickLen, fy], { color, width }));
      if (labels) this._text(katex.renderToString(String(+v.toFixed(6)), { output: 'html' }), { at: [fx - 0.35, fy], size: labelSize, color }, g).style.opacity = '1';
    }
    if (xLabel) this._text(katex.renderToString(xLabel, { output: 'html' }), { at: [c2f(x1, axisY)[0] + 0.3, c2f(x1, axisY)[1] - 0.05], size: 34, color }, g).style.opacity = '1';
    if (yLabel) this._text(katex.renderToString(yLabel, { output: 'html' }), { at: [c2f(axisX, y1)[0] + 0.05, c2f(axisX, y1)[1] + 0.3], size: 34, color }, g).style.opacity = '1';

    const api = { c2f, c2p };
    g.c2f = c2f;
    g.c2p = c2p;
    g.plot = (fn, po = {}) => {
      const [a, b] = po.xRange || [x0, x1];
      const xs = linspace(a, b, po.samples || 400);
      const ys = xs.map((x) => fn(x));
      const path = this._shape('path', { d: curveD(xs.map((x, i) => px(...c2f(x, ys[i])))) }, { color: po.color || COLORS.BLUE, width: po.width ?? 5 });
      path.__plot = { xs, ys, fn, axes: { c2p: (cx, cy) => px(...c2f(cx, cy)) } };
      return path;
    };
    g.area = (fn, [a, b], ao = {}) => {
      const xs = linspace(a, b, ao.samples || 200);
      const pts = [c2f(a, 0), ...xs.map((x) => c2f(x, fn(x))), c2f(b, 0)];
      return this.polygon(pts, { fill: ao.color || COLORS.BLUE, fillOpacity: ao.opacity ?? 0.4, stroke: false });
    };
    g.point = (cx, cy, po = {}) => this.dot(c2f(cx, cy), po);
    g.vector = (v, vo = {}) => this.arrow(c2f(0, 0), c2f(v[0], v[1]), { color: COLORS.YELLOW, ...vo });
    g.line = (a, b, lo = {}) => this.line(c2f(...a), c2f(...b), lo);
    Object.assign(api, g);
    return g;
  }
}
