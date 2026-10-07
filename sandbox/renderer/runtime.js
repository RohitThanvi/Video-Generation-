/*
 * Virtual clock, injected into every page BEFORE its own scripts run.
 *
 * The renderer captures a video one frame at a time. For that to be deterministic (same
 * output every run, no dropped frames however heavy the scene is), page time must not be
 * the wall clock. This file replaces every time source with a clock that only moves when
 * the renderer calls window.__vclock.advance(tMs):
 *
 *   - performance.now(), Date.now(), new Date()
 *   - requestAnimationFrame / cancelAnimationFrame
 *   - setTimeout / setInterval / clearTimeout / clearInterval
 *   - CSS animations, CSS transitions and Web Animations (paused and seeked every frame)
 *
 * So GSAP, three.js (THREE.Clock), anime.js, d3 transitions, CSS keyframes, etc. just work.
 *
 * Pages can opt into full control by defining  window.__renderFrame(tMs, frameIndex)  (it
 * may be async). It is called after every advance, once per captured frame.
 * A page that loads things asynchronously can set  window.__VIDEO_READY = <Promise>  and
 * the renderer waits for it before capturing the first frame.
 *
 * Time starts at 0 when the page loads and is frozen until the first advance().
 */
(() => {
  if (window.__vclock) return;

  const realSetTimeout = window.setTimeout.bind(window);
  const BASE_EPOCH = 1735689600000; // 2025-01-01T00:00:00Z: a fixed wall-clock start

  const S = { t: 0, nextId: 1, seq: 0, raf: new Map(), timers: new Map() };

  // ---- Date / performance.now -------------------------------------------------------
  const NativeDate = Date;
  class VirtualDate extends NativeDate {
    constructor(...args) {
      if (args.length === 0) super(BASE_EPOCH + S.t);
      else super(...args);
    }
    static now() {
      return BASE_EPOCH + S.t;
    }
  }
  window.Date = VirtualDate;
  performance.now = () => S.t;

  // ---- requestAnimationFrame --------------------------------------------------------
  window.requestAnimationFrame = (cb) => {
    const id = S.nextId++;
    S.raf.set(id, cb);
    return id;
  };
  window.cancelAnimationFrame = (id) => {
    S.raf.delete(id);
  };

  // ---- timers -----------------------------------------------------------------------
  function addTimer(cb, delay, args, repeat) {
    if (typeof cb !== 'function') {
      const code = String(cb);
      cb = () => (0, eval)(code);
    }
    delay = Math.max(1, Number(delay) || 0); // >= 1 ms (like browsers' nested clamp) so a 0 ms re-arming timer cannot spin forever
    const id = S.nextId++;
    S.timers.set(id, { cb, args, due: S.t + delay, seq: S.seq++, repeat: repeat ? Math.max(1, delay) : 0 });
    return id;
  }
  window.setTimeout = (cb, delay, ...args) => addTimer(cb, delay, args, false);
  window.setInterval = (cb, delay, ...args) => addTimer(cb, delay, args, true);
  window.clearTimeout = window.clearInterval = (id) => {
    S.timers.delete(id);
  };

  // Errors inside callbacks must still reach window.onerror (the renderer reports them),
  // but must not stop the clock.
  function rethrowAsync(error) {
    realSetTimeout(() => {
      throw error;
    }, 0);
  }

  // ---- CSS animations / transitions / Web Animations --------------------------------
  const born = new WeakMap();
  function syncAnimations() {
    if (!document.getAnimations) return;
    for (const animation of document.getAnimations()) {
      if (!born.has(animation)) born.set(animation, S.t);
      try {
        animation.pause();
        animation.currentTime = S.t - born.get(animation);
      } catch (error) {
        /* an animation may be cancelled while we iterate */
      }
    }
  }

  // ---- advancing --------------------------------------------------------------------
  async function advance(t, frame) {
    if (t < S.t) throw new Error(`virtual time cannot go backwards (${t} < ${S.t})`);

    // 1. timers that fall due, in order, each seeing its own due time as "now"
    for (let guard = 0; guard < 100000; guard++) {
      let nextId = null;
      let next = null;
      for (const [id, timer] of S.timers) {
        if (timer.due > t) continue;
        if (!next || timer.due < next.due || (timer.due === next.due && timer.seq < next.seq)) {
          next = timer;
          nextId = id;
        }
      }
      if (!next) break;
      S.t = Math.max(S.t, next.due);
      if (next.repeat) {
        next.due += next.repeat;
        next.seq = S.seq++;
      } else {
        S.timers.delete(nextId);
      }
      try {
        next.cb(...next.args);
      } catch (error) {
        rethrowAsync(error);
      }
    }
    S.t = t;

    // 2. animations, then animation-frame callbacks (which may start new animations)
    syncAnimations();
    const callbacks = [...S.raf.values()];
    S.raf.clear();
    for (const cb of callbacks) {
      try {
        cb(t);
      } catch (error) {
        rethrowAsync(error);
      }
    }

    // 3. optional page-driven rendering
    if (typeof window.__renderFrame === 'function') {
      try {
        const result = window.__renderFrame(t, frame);
        // Only await real Promises: a GSAP timeline is "thenable" (tl.then resolves when the
        // timeline finishes), so `__renderFrame = t => tl.time(t)` must not be awaited.
        if (result instanceof Promise) await result;
      } catch (error) {
        rethrowAsync(error);
      }
    }

    // 4. let promise continuations run, then catch animations created by them
    await new Promise((resolve) => realSetTimeout(resolve, 0));
    syncAnimations();
  }

  window.__vclock = { advance, now: () => S.t, realSetTimeout };
})();
