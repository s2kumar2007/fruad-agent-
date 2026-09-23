(function () {
  var P = window.parent, D = P.document;
  if (P.__fraudFx) return;
  P.__fraudFx = true;

  // 1) drifting graph network background with travelling packets
  var cv = D.createElement('canvas');
  cv.id = 'net';
  cv.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;z-index:0;pointer-events:none';
  D.querySelector('.stApp').prepend(cv);
  var x = cv.getContext('2d'), W, H, N = [], Q = [];
  var still = P.matchMedia('(prefers-reduced-motion:reduce)').matches;
  function resize() {
    W = cv.width = P.innerWidth; H = cv.height = P.innerHeight;
    N = Array.from({ length: Math.min(55, W / 26 | 0) }, function () {
      return { x: Math.random() * W, y: Math.random() * H, vx: (Math.random() - .5) * .18, vy: (Math.random() - .5) * .18 };
    });
  }
  resize(); P.addEventListener('resize', resize);
  function draw() {
    x.clearRect(0, 0, W, H);
    N.forEach(function (a) {
      if (!still) { a.x += a.vx; a.y += a.vy; if (a.x < 0 || a.x > W) a.vx *= -1; if (a.y < 0 || a.y > H) a.vy *= -1; }
      N.forEach(function (b) {
        var d = Math.hypot(a.x - b.x, a.y - b.y);
        if (d < 150) { x.strokeStyle = 'rgba(140,170,230,' + (.16 * (1 - d / 150)) + ')'; x.beginPath(); x.moveTo(a.x, a.y); x.lineTo(b.x, b.y); x.stroke(); }
      });
      x.fillStyle = 'rgba(140,170,230,.5)'; x.beginPath(); x.arc(a.x, a.y, 1.8, 0, 7); x.fill();
    });
    if (!still) {
      if (Q.length < 7 && Math.random() < .04) {
        var a = N[Math.random() * N.length | 0];
        var b = N.find(function (o) { return o !== a && Math.hypot(a.x - o.x, a.y - o.y) < 150; });
        if (b) Q.push({ a: a, b: b, t: 0 });
      }
      for (var i = Q.length - 1; i >= 0; i--) {
        var q = Q[i]; q.t += .012;
        if (q.t >= 1) { Q.splice(i, 1); continue; }
        x.fillStyle = 'rgba(91,140,255,.9)'; x.shadowColor = '#5b8cff'; x.shadowBlur = 10;
        x.beginPath(); x.arc(q.a.x + (q.b.x - q.a.x) * q.t, q.a.y + (q.b.y - q.a.y) * q.t, 2.4, 0, 7); x.fill(); x.shadowBlur = 0;
      }
      P.requestAnimationFrame(draw);
    }
  }
  draw();

  // 2) count-up for any <b class="cu" data-n=".." data-d=".." data-pre="..">, also after Streamlit reruns
  function countUp(el) {
    if (el.dataset.done) return; el.dataset.done = 1;
    var to = +el.dataset.n, d = +(el.dataset.d || 0), pre = el.dataset.pre || '', t0 = performance.now();
    if (still) { el.textContent = pre + to.toFixed(d); return; }
    (function f(t) {
      var k = Math.min(1, (t - t0) / 900), e = 1 - Math.pow(1 - k, 3);
      el.textContent = pre + (to * e).toFixed(d);
      if (k < 1) P.requestAnimationFrame(f);
    })(t0);
  }
  function scan() { D.querySelectorAll('b.cu:not([data-done])').forEach(countUp); }
  new P.MutationObserver(scan).observe(D.body, { childList: true, subtree: true });
  scan();
})();
