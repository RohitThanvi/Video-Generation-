/* Motion-graphics engine for the video kit. Installed as source/fx.js (after kit.js).
 * Everything is a pure function of time (performance.now() = the renderer's virtual clock) and a
 * seeded random generator, so every frame renders identically on every run.
 *
 *  <body data-theme="sunset" data-bg="aurora" data-post="vignette grain leak">
 *  data-bg   : particles | network | stars | aurora | grid | waves | rain | none
 *  data-post : any of vignette grain scan leak (default "vignette grain"). vignette is free; grain/leak/scan
 *              add a full-screen overlay (~+30% render time each frame set), use them when the look is worth it.
 *  <section class="scene" data-cam="push|pull|left|right|drift|tilt|none">   (default: varies per scene)
 *  <h1 data-split="chars|words" data-kin="rise|drop|blur|flip|wave|zoom|spin|glitch">
 *  <div class="fx-bars" data-values="30,62,88" data-labels="A,B,C" data-suffix="%"></div>
 *  <div class="fx-donut" data-value="72" data-suffix="%" data-label="Done"></div>
 *  <div class="fx-line" data-values="3,5,4,9,14"></div>
 *  <div class="hero3d" data-shape="torusknot|icosa|globe|crystal|rings" style="--size:640px"></div>
 */
(function () {
  'use strict';
  var W = 1920, H = 1080;

  function rng(seed) { // mulberry32
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      var t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function css(name, fallback) {
    var v = getComputedStyle(document.body).getPropertyValue(name).trim();
    return v || fallback;
  }
  function rgba(hex, a) {
    var m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
    if (!m) return hex;
    var n = parseInt(m[1], 16);
    return 'rgba(' + (n >> 16) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a + ')';
  }
  function now() { return performance.now() / 1000; }

  /* ------------------------------------------------------------------ background canvas */
  var BACKGROUNDS = {
    particles: function (ctx, c) {
      var r = rng(11), dots = [];
      for (var i = 0; i < 150; i++) dots.push({x: r() * W, y: r() * H, z: .25 + r() * .75, p: r() * 6.28, k: r()});
      return function (t) {
        dots.forEach(function (d) {
          var x = (d.x + t * 22 * d.z) % W, y = (d.y - t * 34 * d.z + H * 4) % H;
          x += Math.sin(t * .5 + d.p) * 30 * d.z;
          var a = (.25 + .6 * (.5 + .5 * Math.sin(t * 1.4 + d.p))) * d.z;
          var rad = 2 + 7 * d.z;
          var g = ctx.createRadialGradient(x, y, 0, x, y, rad * 4);
          var col = d.k < .5 ? c.a : d.k < .85 ? c.b : c.c;
          g.addColorStop(0, rgba(col, a)); g.addColorStop(1, rgba(col, 0));
          ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, rad * 4, 0, 6.2832); ctx.fill();
        });
      };
    },
    network: function (ctx, c) {
      var r = rng(5), n = [];
      for (var i = 0; i < 70; i++) n.push({x: r() * W, y: r() * H, a: r() * 6.28, s: 10 + r() * 22, p: r() * 6.28});
      return function (t) {
        var pts = n.map(function (q) {
          return {x: (q.x + Math.cos(q.a) * q.s * t + W * 8) % W, y: (q.y + Math.sin(q.a) * q.s * t + H * 8) % H, p: q.p};
        });
        ctx.lineWidth = 1.5;
        for (var i = 0; i < pts.length; i++) {
          for (var j = i + 1; j < pts.length; j++) {
            var dx = pts[i].x - pts[j].x, dy = pts[i].y - pts[j].y, d = Math.sqrt(dx * dx + dy * dy);
            if (d < 260) { ctx.strokeStyle = rgba(c.a, (1 - d / 260) * .45); ctx.beginPath(); ctx.moveTo(pts[i].x, pts[i].y); ctx.lineTo(pts[j].x, pts[j].y); ctx.stroke(); }
          }
        }
        pts.forEach(function (q) {
          ctx.fillStyle = rgba(c.b, .5 + .4 * Math.sin(t * 2 + q.p)); ctx.beginPath(); ctx.arc(q.x, q.y, 4.5, 0, 6.2832); ctx.fill();
        });
      };
    },
    stars: function (ctx, c) {
      var r = rng(3), s = [];
      for (var i = 0; i < 380; i++) s.push({x: (r() - .5) * 2, y: (r() - .5) * 2, z: r(), k: r()});
      return function (t) {
        s.forEach(function (q) {
          var z = ((q.z - t * .07) % 1 + 1) % 1, k = 1 / (z + .02) * .12;
          var x = W / 2 + q.x * W * k * .55, y = H / 2 + q.y * H * k * .55;
          if (x < -50 || x > W + 50 || y < -50 || y > H + 50) return;
          var len = Math.min(70, 3 + (1 - z) * 60), a = Math.min(1, (1 - z) * 1.4);
          var dx = x - W / 2, dy = y - H / 2, m = Math.sqrt(dx * dx + dy * dy) || 1;
          ctx.strokeStyle = rgba(q.k < .6 ? '#ffffff' : q.k < .8 ? c.a : c.b, a);
          ctx.lineWidth = .8 + (1 - z) * 2.4;
          ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x - dx / m * len * .5, y - dy / m * len * .5); ctx.stroke();
        });
      };
    },
    aurora: function (ctx, c) {
      var cols = [c.a, c.b, c.c];
      return function (t) {
        ctx.globalCompositeOperation = 'lighter';
        for (var k = 0; k < 3; k++) {
          var g = ctx.createLinearGradient(0, H * .15, 0, H * .85);
          g.addColorStop(0, rgba(cols[k], 0)); g.addColorStop(.5, rgba(cols[k], .22)); g.addColorStop(1, rgba(cols[k], 0));
          ctx.fillStyle = g; ctx.beginPath(); ctx.moveTo(0, H);
          for (var x = 0; x <= W; x += 24) {
            var y = H * (.42 + k * .09) + Math.sin(x * .0042 + t * (.5 + k * .17) + k * 2) * 130 + Math.sin(x * .0091 - t * .8 + k) * 55;
            ctx.lineTo(x, y);
          }
          ctx.lineTo(W, H); ctx.closePath(); ctx.fill();
        }
        ctx.globalCompositeOperation = 'source-over';
      };
    },
    grid: function (ctx, c) {
      return function (t) {
        var hy = H * .52;
        var sun = ctx.createRadialGradient(W / 2, hy - 40, 20, W / 2, hy - 40, 330);
        sun.addColorStop(0, rgba(c.c, .85)); sun.addColorStop(.55, rgba(c.b, .5)); sun.addColorStop(1, rgba(c.b, 0));
        ctx.fillStyle = sun; ctx.beginPath(); ctx.arc(W / 2, hy - 40, 330, 0, 6.2832); ctx.fill();
        ctx.lineWidth = 2;
        for (var i = 0; i < 22; i++) { // horizontal lines rushing toward the camera
          var p = ((i + t * .9) % 22) / 22, y = hy + Math.pow(p, 2.2) * (H - hy);
          ctx.strokeStyle = rgba(c.a, .12 + p * .55); ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
        }
        for (var j = -16; j <= 16; j++) {
          ctx.strokeStyle = rgba(c.a, .38); ctx.beginPath(); ctx.moveTo(W / 2 + j * 36, hy); ctx.lineTo(W / 2 + j * 260, H); ctx.stroke();
        }
      };
    },
    waves: function (ctx, c) {
      var cols = [c.a, c.b, c.c, c.a];
      return function (t) {
        for (var k = 0; k < 4; k++) {
          ctx.fillStyle = rgba(cols[k], .13); ctx.beginPath(); ctx.moveTo(0, H);
          for (var x = 0; x <= W; x += 20) ctx.lineTo(x, H * (.62 + k * .07) + Math.sin(x * .0065 + t * (.7 + k * .2) + k * 1.7) * (60 + k * 16));
          ctx.lineTo(W, H); ctx.closePath(); ctx.fill();
        }
      };
    },
    rain: function (ctx, c) {
      var r = rng(8), cols = [];
      for (var i = 0; i < 70; i++) cols.push({x: i * (W / 70) + 6, s: 90 + r() * 280, o: r() * H, l: 8 + Math.floor(r() * 14)});
      var glyphs = '01<>/{}=+*$#';
      ctx.font = '22px monospace';
      return function (t) {
        cols.forEach(function (q, ci) {
          var head = (q.o + t * q.s) % (H + q.l * 26);
          for (var k = 0; k < q.l; k++) {
            ctx.fillStyle = rgba(k === 0 ? '#ffffff' : c.a, (1 - k / q.l) * .55);
            ctx.fillText(glyphs[(ci * 7 + k * 3 + Math.floor(t * 6 + k)) % glyphs.length], q.x, head - k * 26);
          }
        });
      };
    }
  };

  function setupBackground() {
    var kind = document.body.getAttribute('data-bg') || 'particles';
    if (kind === 'none' || !BACKGROUNDS[kind]) return;
    var host = document.querySelector('.bg');
    if (!host) { host = document.createElement('div'); host.className = 'bg'; document.body.insertBefore(host, document.body.firstChild); }
    var cv = document.createElement('canvas');
    cv.className = 'fx-canvas';
    cv.width = W / 2; cv.height = H / 2; // soft glow layers do not need full resolution
    host.appendChild(cv);
    var ctx = cv.getContext('2d', {alpha: false, willReadFrequently: true}); // opaque, CPU-backed: composites far faster in software
    var colors = {a: css('--accent', '#6ea8ff'), b: css('--accent-2', '#a78bfa'), c: css('--accent-3', '#2dd4bf')};
    var draw = BACKGROUNDS[kind](ctx, colors);
    document.body.classList.add('fx-bg-on');
    // The vignette is painted into this (opaque, bottom) canvas: free, unlike another full-screen layer.
    var vignetteOn = (document.body.getAttribute('data-post') || 'vignette grain').split(/\s+/).indexOf('vignette') >= 0;
    var vignette = ctx.createRadialGradient(cv.width / 2, cv.height / 2, cv.height * .55, cv.width / 2, cv.height / 2, cv.width * .62);
    vignette.addColorStop(0, 'rgba(0,0,0,0)'); vignette.addColorStop(1, 'rgba(0,0,0,.6)');
    var dots = document.createElement('canvas'); dots.width = W; dots.height = H; // static dot grid, drawn once
    (function () {
      var dx = dots.getContext('2d'); dx.fillStyle = 'rgba(255,255,255,.08)';
      for (var y = 23; y < H; y += 46) for (var x = 23; x < W; x += 46) dx.fillRect(x, y, 2, 2);
      dx.globalCompositeOperation = 'destination-in';
      var m = dx.createRadialGradient(W / 2, H / 2, 0, W / 2, H / 2, W * .55);
      m.addColorStop(.25, 'rgba(0,0,0,1)'); m.addColorStop(.8, 'rgba(0,0,0,0)');
      dx.fillStyle = m; dx.fillRect(0, 0, W, H);
    })();
    var orbs = [[-.1, -.05, 1100, colors.a, .5, 22, 0], [1.05, 1.0, 1000, colors.b, .45, 27, 2], [.5, .45, 760, colors.c, .26, 31, 4]];
    function drawOrbs(t) {
      orbs.forEach(function (o) {
        var x = o[0] * W + Math.sin(t / o[5] * 3.14 + o[6]) * 140, y = o[1] * H + Math.cos(t / o[5] * 3.14 + o[6]) * 90, r = o[2] * .5;
        var g = ctx.createRadialGradient(x, y, 0, x, y, r);
        g.addColorStop(0, rgba(o[3], o[4])); g.addColorStop(1, rgba(o[3], 0));
        ctx.fillStyle = g; ctx.fillRect(x - r, y - r, r * 2, r * 2);
      });
    }
    var c0 = css('--bg-0', '#070b17'), c1 = css('--bg-1', '#0d1430'), c2b = css('--bg-2', '#162050');
    function frame() {
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      var base = ctx.createLinearGradient(0, 0, cv.width * .55, cv.height);
      base.addColorStop(0, c2b); base.addColorStop(.45, c1); base.addColorStop(1, c0);
      ctx.fillStyle = base; ctx.fillRect(0, 0, cv.width, cv.height);
      ctx.scale(.5, .5);
      drawOrbs(now());
      ctx.drawImage(dots, 0, 0);
      draw(now());
      if (vignetteOn) { ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.fillStyle = vignette; ctx.fillRect(0, 0, cv.width, cv.height); }
      requestAnimationFrame(frame);
    }
    frame();
  }

  /* ------------------------------------------------------------------ post layers */
  // Vignette, film grain, light leak and scanlines are drawn into ONE overlay canvas (12 updates
  // per second, deterministic): a single cheap layer instead of several full-screen blend layers.
  function setupPost() {
    var flags = (document.body.getAttribute('data-post') || 'vignette grain').split(/\s+/);
    if (flags.indexOf('none') >= 0 || !flags.some(function (f) { return /^(grain|leak|scan)$/.test(f) || (f === 'vignette' && !document.body.classList.contains('fx-bg-on')); })) return;
    var PW = 960, PH = 540, cv = document.createElement('canvas');
    cv.width = PW; cv.height = PH; cv.className = 'fx-post-canvas';
    document.body.appendChild(cv);
    var ctx = cv.getContext('2d', {willReadFrequently: true}), has = function (f) { return flags.indexOf(f) >= 0; };
    var c2 = css('--accent-2', '#a78bfa'), c3 = css('--accent-3', '#2dd4bf'), lastStep = -1;
    var vig = ctx.createRadialGradient(PW / 2, PH / 2, PH * .55, PW / 2, PH / 2, PW * .62);
    vig.addColorStop(0, 'rgba(0,0,0,0)'); vig.addColorStop(1, 'rgba(0,0,0,.55)');
    var noise = document.createElement('canvas'); noise.width = 240; noise.height = 135;
    var nctx = noise.getContext('2d'), nimg = nctx.createImageData(240, 135);
    (function loop() {
      var t = now(), step = Math.floor(t * 12);
      if (step !== lastStep) {
        lastStep = step;
        ctx.clearRect(0, 0, PW, PH);
        if (has('leak')) {
          var ph = (t % 11) / 11, k = ph < .5 ? ph * 2 : 2 - ph * 2, cx = PW * (-.1 + 1.2 * k);
          var g = ctx.createLinearGradient(cx - 260, 0, cx + 260, 0);
          g.addColorStop(0, rgba(c2, 0)); g.addColorStop(.45, rgba(c2, .2 * Math.sin(k * 3.14))); g.addColorStop(.6, rgba(c3, .16 * Math.sin(k * 3.14))); g.addColorStop(1, rgba(c3, 0));
          ctx.fillStyle = g; ctx.fillRect(0, 0, PW, PH);
        }
        if (has('scan')) { ctx.fillStyle = 'rgba(255,255,255,.03)'; for (var y = 0; y < PH; y += 2) ctx.fillRect(0, y, PW, 1); }
        if (has('grain')) {
          var r = rng(step * 977 + 1), d = nimg.data;
          for (var i = 0; i < d.length; i += 4) { var v = r() * 255; d[i] = d[i + 1] = d[i + 2] = v; d[i + 3] = 11; }
          nctx.putImageData(nimg, 0, 0);
          ctx.imageSmoothingEnabled = false; ctx.drawImage(noise, 0, 0, PW, PH);
        }
        if (has('vignette') && !document.body.classList.contains('fx-bg-on')) { ctx.fillStyle = vig; ctx.fillRect(0, 0, PW, PH); }
      }
      requestAnimationFrame(loop);
    })();
  }

  /* ------------------------------------------------------------------ camera + text */
  function setupCamera() {
    var moves = ['push', 'drift', 'pull', 'left', 'tilt', 'right'];
    document.querySelectorAll('.scene').forEach(function (scene, i) {
      if (!scene.getAttribute('data-cam')) scene.setAttribute('data-cam', moves[i % moves.length]);
      if (scene.querySelector(':scope > .cam')) return;
      var cam = document.createElement('div');
      cam.className = 'cam';
      while (scene.firstChild) cam.appendChild(scene.firstChild);
      scene.appendChild(cam);
    });
  }

  function splitText() {
    document.querySelectorAll('[data-split]').forEach(function (el) {
      var mode = el.getAttribute('data-split') === 'words' ? 'wd' : 'ch', index = 0;
      (function walk(node) {
        Array.prototype.slice.call(node.childNodes).forEach(function (child) {
          if (child.nodeType === 3) {
            var frag = document.createDocumentFragment();
            var parts = mode === 'wd' ? child.nodeValue.split(/(\s+)/) : Array.from(child.nodeValue);
            parts.forEach(function (part) {
              if (part === '') return;
              if (/^\s+$/.test(part)) { frag.appendChild(document.createTextNode(part)); return; }
              var span = document.createElement('span');
              span.className = mode; span.style.setProperty('--i', index++); span.textContent = part;
              frag.appendChild(span);
            });
            child.parentNode.replaceChild(frag, child);
          } else if (child.nodeType === 1) walk(child);
        });
      })(el);
    });
  }

  /* ------------------------------------------------------------------ infographics */
  function nums(el) { return (el.getAttribute('data-values') || '').split(',').map(parseFloat).filter(function (v) { return !isNaN(v); }); }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }

  function buildCharts() {
    document.querySelectorAll('.fx-bars').forEach(function (el) {
      var vals = nums(el), labels = (el.getAttribute('data-labels') || '').split(','), max = Math.max.apply(null, vals) || 1;
      var suf = el.getAttribute('data-suffix') || '', pre = el.getAttribute('data-prefix') || '';
      el.innerHTML = vals.map(function (v, i) {
        var hgt = 'calc((var(--h,420px) - 110px) * ' + (v / max).toFixed(3) + ')';
        return '<div class="b" style="--i:' + i + '"><span class="v" data-count="' + v + '" data-prefix="' + esc(pre) + '" data-suffix="' + esc(suf) +
          '" style="--d:' + (i * .16 + .5).toFixed(2) + 's"></span><div class="fill" style="height:' + hgt + '"></div><span class="l">' +
          esc((labels[i] || '').trim()) + '</span></div>';
      }).join('');
    });
    document.querySelectorAll('.fx-donut').forEach(function (el) {
      var v = Math.max(0, Math.min(100, parseFloat(el.getAttribute('data-value') || '0'))) / 100;
      var suf = el.getAttribute('data-suffix') || '%', label = el.getAttribute('data-label') || '';
      el.innerHTML = '<svg viewBox="0 0 100 100"><defs><linearGradient id="dg' + Math.random().toString(36).slice(2, 6) + '"></linearGradient></defs>' +
        '<circle class="track" cx="50" cy="50" r="42" fill="none" stroke-width="9"/>' +
        '<circle class="arc" cx="50" cy="50" r="42" fill="none" stroke-width="9" pathLength="1" stroke="var(--accent)" style="stroke-dasharray:' + v + ' 1;filter:drop-shadow(0 0 3px var(--accent-2))"/></svg>' +
        '<div style="text-align:center"><div class="num" data-count="' + (v * 100) + '" data-suffix="' + esc(suf) + '"></div>' +
        (label ? '<div class="label">' + esc(label) + '</div>' : '') + '</div>';
    });
    document.querySelectorAll('.fx-line').forEach(function (el) {
      var vals = nums(el), w = 1000, h = 420, pad = 40;
      var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals), span = (hi - lo) || 1;
      var pts = vals.map(function (v, i) { return [pad + i * (w - 2 * pad) / Math.max(1, vals.length - 1), h - pad - (v - lo) / span * (h - 2 * pad)]; });
      var d = 'M' + pts[0][0] + ',' + pts[0][1];
      for (var i = 1; i < pts.length; i++) { // smooth curve through the points
        var p0 = pts[i - 1], p1 = pts[i], mx = (p0[0] + p1[0]) / 2;
        d += ' C' + mx + ',' + p0[1] + ' ' + mx + ',' + p1[1] + ' ' + p1[0] + ',' + p1[1];
      }
      var gid = 'lg' + Math.random().toString(36).slice(2, 6);
      el.innerHTML = '<svg viewBox="0 0 ' + w + ' ' + h + '" width="100%" height="100%" style="overflow:visible"><defs>' +
        '<linearGradient id="' + gid + 'a" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="var(--accent)" stop-opacity=".5"/><stop offset="1" stop-color="var(--accent)" stop-opacity="0"/></linearGradient>' +
        '<linearGradient id="' + gid + 'b" x1="0" x2="1"><stop offset="0" stop-color="var(--accent)"/><stop offset="1" stop-color="var(--accent-3)"/></linearGradient></defs>' +
        '<path class="area" d="' + d + ' L' + pts[pts.length - 1][0] + ',' + (h - pad) + ' L' + pts[0][0] + ',' + (h - pad) + ' Z" fill="url(#' + gid + 'a)"/>' +
        '<path class="ln" d="' + d + '" pathLength="1" fill="none" stroke="url(#' + gid + 'b)" stroke-width="7" stroke-linecap="round" style="filter:drop-shadow(0 0 10px var(--accent))"/>' +
        pts.map(function (p, i) { return '<circle class="dt" style="--i:' + i + ';transform-origin:' + p[0] + 'px ' + p[1] + 'px" cx="' + p[0] + '" cy="' + p[1] + '" r="11" fill="#fff" stroke="var(--accent)" stroke-width="5"/>'; }).join('') + '</svg>';
    });
  }

  /* ------------------------------------------------------------------ 3D hero objects (three.js) */
  function setupHeroes() {
    var heroes = Array.prototype.slice.call(document.querySelectorAll('.hero3d'));
    if (!heroes.length) return;
    // A live WebGL context makes every screenshot in the software renderer several seconds slower,
    // so a hero object exists (context, geometry, env map) only while its scene is on screen.
    var ready = import('three').then(function (THREE) {
      function build(el, idx) {
        var size = el.clientWidth || 640;
        var renderer = new THREE.WebGLRenderer({antialias: true, alpha: true, preserveDrawingBuffer: true});
        renderer.setPixelRatio(1); renderer.setSize(size, el.clientHeight || size);
                el.appendChild(renderer.domElement);
        var scene = new THREE.Scene();
        var camera = new THREE.PerspectiveCamera(38, 1, .1, 100); camera.position.set(0, 0, 9.4);
        var accent = new THREE.Color(css('--accent', '#6ea8ff')), accent2 = new THREE.Color(css('--accent-2', '#a78bfa')), accent3 = new THREE.Color(css('--accent-3', '#2dd4bf'));
        var group = new THREE.Group(); scene.add(group);
        var shape = el.getAttribute('data-shape') || 'torusknot', update;
        // Matcap shading: a painted "sphere of light" looked up by surface normal. It looks like a
        // glossy studio render but costs one texture read per pixel (environment maps are several
        // times slower on the software GL renderer, and the lights/env are not needed at all).
        var glossy = function (c, extra) {
          var cv = document.createElement('canvas'); cv.width = cv.height = 256;
          var g = cv.getContext('2d'), col = new THREE.Color(c), hex = function (k) { return '#' + k.getHexString(); };
          var light = col.clone().lerp(new THREE.Color(0xffffff), .55), dark = col.clone().multiplyScalar(.28);
          var body = g.createRadialGradient(100, 86, 6, 128, 128, 128);
          body.addColorStop(0, '#ffffff'); body.addColorStop(.12, hex(light)); body.addColorStop(.5, hex(col)); body.addColorStop(.9, hex(dark)); body.addColorStop(1, '#05060a');
          g.fillStyle = body; g.fillRect(0, 0, 256, 256);
          var rimg = g.createRadialGradient(190, 214, 4, 190, 214, 110);   // coloured rim light, lower right
          rimg.addColorStop(0, hex(accent3) + 'cc'); rimg.addColorStop(1, hex(accent3) + '00');
          g.globalCompositeOperation = 'lighter'; g.fillStyle = rimg; g.fillRect(0, 0, 256, 256);
          var tex = new THREE.CanvasTexture(cv); tex.colorSpace = THREE.SRGBColorSpace;
          return new THREE.MeshMatcapMaterial(Object.assign({matcap: tex}, extra || {}));
        };
        if (shape === 'icosa') {
          var core = new THREE.Mesh(new THREE.IcosahedronGeometry(1.5, 1), glossy(accent, {flatShading: true}));
          var wire = new THREE.Mesh(new THREE.IcosahedronGeometry(2.25, 1), new THREE.MeshBasicMaterial({color: accent3, wireframe: true, transparent: true, opacity: .55}));
          group.add(core, wire);
          update = function (t) { core.rotation.set(t * .5, t * .7, 0); wire.rotation.set(-t * .3, t * .4, 0); };
        } else if (shape === 'globe') {
          var ball = new THREE.Mesh(new THREE.SphereGeometry(2, 64, 48), glossy(accent2));
          var lat = new THREE.Mesh(new THREE.SphereGeometry(2.04, 36, 24), new THREE.MeshBasicMaterial({color: accent3, wireframe: true, transparent: true, opacity: .35}));
          var r = rng(21), pg = new THREE.BufferGeometry(), pp = [];
          for (var i = 0; i < 420; i++) { var u = r() * 6.2832, v = Math.acos(2 * r() - 1), rad = 2.75 + r() * 1.1; pp.push(rad * Math.sin(v) * Math.cos(u), rad * Math.cos(v), rad * Math.sin(v) * Math.sin(u)); }
          pg.setAttribute('position', new THREE.Float32BufferAttribute(pp, 3));
          var cloud = new THREE.Points(pg, new THREE.PointsMaterial({color: 0xffffff, size: .05, transparent: true, opacity: .85}));
          group.add(ball, lat, cloud);
          update = function (t) { ball.rotation.y = t * .35; lat.rotation.y = t * .35; cloud.rotation.y = -t * .12; cloud.rotation.x = t * .05; group.rotation.z = .35; };
        } else if (shape === 'crystal') {
          var shards = [];
          [[0, 0, 0, 1.6], [1.9, .8, -.6, .8], [-1.8, -.7, .4, .9], [.6, -1.9, .8, .6], [-.9, 1.8, -.5, .55]].forEach(function (s, i) {
            var m = new THREE.Mesh(new THREE.OctahedronGeometry(s[3], 0), glossy([accent, accent2, accent3][i % 3], {flatShading: true}));
            m.scale.y = 1.7; m.position.set(s[0], s[1], s[2]); m.userData = {p: i * 1.3, b: s}; group.add(m); shards.push(m);
          });
          update = function (t) { shards.forEach(function (m, i) { m.rotation.y = t * (.5 + i * .12); m.rotation.z = Math.sin(t * .6 + m.userData.p) * .25; m.position.y = m.userData.b[1] + Math.sin(t * .9 + m.userData.p) * .22; }); };
        } else if (shape === 'rings') {
          var rings = [accent, accent2, accent3].map(function (c, i) {
            var m = new THREE.Mesh(new THREE.TorusGeometry(1.2 + i * .62, .09, 24, 160), glossy(c)); group.add(m); return m;
          });
          var orb = new THREE.Mesh(new THREE.SphereGeometry(.7, 48, 32), glossy(0xffffff)); group.add(orb);
          update = function (t) { rings.forEach(function (m, i) { m.rotation.x = t * (.6 - i * .15) + i; m.rotation.y = t * (.4 + i * .2); }); orb.scale.setScalar(1 + .06 * Math.sin(t * 2)); };
        } else { // torusknot
          var knot = new THREE.Mesh(new THREE.TorusKnotGeometry(1.55, .52, 360, 48, 2, 3), glossy(accent));
          group.add(knot);
          update = function (t) { knot.rotation.set(t * .35, t * .55, Math.sin(t * .4) * .3); };
        }
        return {
          draw: function (t) {
            group.position.y = Math.sin(t * 1.1 + idx) * .12;
            update(t + idx * 1.7);
            renderer.render(scene, camera);
          },
          dispose: function () {
            scene.traverse(function (o) { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose(); });
            renderer.dispose(); renderer.forceContextLoss();
            if (renderer.domElement.parentNode) renderer.domElement.parentNode.removeChild(renderer.domElement);
          }
        };
      }
      var live = heroes.map(function () { return null; });
      (function loop() {
        var t = now();
        heroes.forEach(function (el, i) {
          var sc = el.closest('.scene'), on = !sc || sc.classList.contains('is-active');
          if (on && !live[i]) live[i] = build(el, i);
          else if (!on && live[i]) { live[i].dispose(); live[i] = null; }
          if (live[i]) live[i].draw(t);
        });
        requestAnimationFrame(loop);
      })();
    });
    window.__VIDEO_READY = Promise.all([window.__VIDEO_READY, ready]);
  }

  function init() {
    setupCamera(); splitText(); buildCharts(); setupBackground(); setupPost(); setupHeroes();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
