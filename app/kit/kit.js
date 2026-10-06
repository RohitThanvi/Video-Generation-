/* Scene engine for the HTML video compiler. Installed as source/kit.js.
 * - Scene durations come from the storyboard: render.py injects window.__VIDEO_SCENES__
 *   ([{id,duration}]) and scenes are matched by <section class="scene" data-scene="ID">.
 *   (data-duration="N" is only a fallback for opening the page by hand.)
 * - Time is performance.now() (ms since navigation), the same clock render.py uses to
 *   align the recording and the narration, so visuals and audio cannot drift apart.
 * - Scene i is shown from its start; at its end the NEXT scene starts and scene i
 *   cross-fades out over TRANSITION seconds, so there are never hard cuts.
 */
(function () {
  var TRANSITION = 0.8; // keep equal to --t in kit.css

  var ICONS = {
    rocket: '<path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0"/><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/>',
    globe: '<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
    moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9z"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/>',
    star: '<path d="m12 2 3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01z"/>',
    zap: '<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/>',
    clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    arrow: '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
    chart: '<path d="M18 20V10"/><path d="M12 20V4"/><path d="M6 20v-6"/>',
    users: '<circle cx="9" cy="7" r="4"/><path d="M3 21v-2a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v2"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/><path d="M21 21v-2a4 4 0 0 0-3-3.87"/>',
    lightbulb: '<path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5"/><path d="M9 18h6"/><path d="M10 22h4"/>',
    target: '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
    layers: '<path d="m12 2 10 5-10 5L2 7z"/><path d="m2 17 10 5 10-5"/><path d="m2 12 10 5 10-5"/>',
    shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    flag: '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><path d="M4 22v-7"/>',
    play: '<polygon points="6 3 20 12 6 21 6 3"/>',
    satellite: '<g transform="rotate(-45 12 12)"><rect x="9.5" y="9.5" width="5" height="5" rx="1"/><rect x="1.5" y="8" width="6" height="8" rx="1"/><rect x="16.5" y="8" width="6" height="8" rx="1"/><path d="M7.5 12h2M14.5 12h2M12 9.5V6.5"/></g>'
  };

  function hydrateIcons(root) {
    root.querySelectorAll('[data-icon]').forEach(function (el) {
      var body = ICONS[el.getAttribute('data-icon')];
      if (!body || el.querySelector('svg')) return;
      el.insertAdjacentHTML('beforeend',
        '<svg viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" ' +
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">' + body + '</svg>');
    });
  }

  function easeOut(p) { return 1 - Math.pow(1 - p, 4); }

  // <span data-count="1969" data-prefix="" data-suffix="" data-decimals="0" data-group="0">
  function startCounters(scene, now) {
    scene.querySelectorAll('[data-count]').forEach(function (el) {
      var to = parseFloat(el.getAttribute('data-count'));
      if (isNaN(to)) return;
      var dec = parseInt(el.getAttribute('data-decimals') || '0', 10);
      var pre = el.getAttribute('data-prefix') || '', suf = el.getAttribute('data-suffix') || '';
      var group = el.getAttribute('data-group') !== '0' && Math.abs(to) >= 10000;
      var delay = parseFloat((getComputedStyle(el).getPropertyValue('--d') || '0').replace('s', '')) || 0;
      var dur = parseFloat(el.getAttribute('data-count-duration') || '1.8');
      var t0 = now + delay;
      function fmt(v) {
        var s = v.toFixed(dec);
        return pre + (group ? Number(s).toLocaleString('en-US', {minimumFractionDigits: dec}) : s) + suf;
      }
      el.textContent = fmt(0);
      (function step() {
        var p = (performance.now() / 1000 - t0) / dur;
        if (p < 0) { requestAnimationFrame(step); return; }
        el.textContent = fmt(to * easeOut(Math.min(p, 1)));
        if (p < 1) requestAnimationFrame(step);
      })();
    });
  }

  function init() {
    hydrateIcons(document);
    var scenes = Array.prototype.slice.call(document.querySelectorAll('.scene'));
    var plan = window.__VIDEO_SCENES__ || [];
    var byId = {};
    plan.forEach(function (s) { byId[s.id] = s; });

    var cursor = 0;
    var timeline = scenes.map(function (el, i) {
      var p = byId[el.getAttribute('data-scene')] || plan[i];
      var dur = p ? Number(p.duration) : parseFloat(el.getAttribute('data-duration'));
      if (!(dur > 0)) {
        console.error('Scene ' + (el.getAttribute('data-scene') || i) + ' has no duration. ' +
          'Give it data-scene="<storyboard id>" matching a storyboard scene.');
        dur = 3;
      }
      var entry = {el: el, start: cursor, end: cursor + dur, state: 'idle'};
      cursor += dur;
      return entry;
    });
    if (window.__VIDEO_TOTAL__ && Math.abs(window.__VIDEO_TOTAL__ - cursor) > 0.05) {
      console.error('HTML has ' + scenes.length + ' scenes totalling ' + cursor.toFixed(2) +
        's but the storyboard totals ' + Number(window.__VIDEO_TOTAL__).toFixed(2) + 's.');
    }

    var bar = document.querySelector('.progress');
    var last = timeline.length - 1;

    function tick() {
      var t = performance.now() / 1000;
      timeline.forEach(function (s, i) {
        var el = s.el;
        if (t >= s.start && s.state === 'idle') {
          s.state = 'active';
          el.classList.add('is-active');
          startCounters(el, t);
        }
        // The last scene holds its final frame; every other scene fades out at its end.
        if (i < last && t >= s.end && s.state === 'active') {
          s.state = 'leaving';
          el.classList.add('is-leaving');
        }
        if (s.state === 'leaving' && t >= s.end + TRANSITION) {
          s.state = 'done';
          el.classList.remove('is-active', 'is-leaving');
        }
      });
      if (bar) bar.style.transform = 'scaleX(' + Math.min(1, t / (cursor || 1)) + ')';
      requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  window.Kit = {TRANSITION: TRANSITION, icons: ICONS};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
