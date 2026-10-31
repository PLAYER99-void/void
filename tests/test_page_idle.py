"""Tests for Void at rest: hidden (the shell's voidHidden, or the page itself hidden), his face's frame loop stops
asking for frames at all; shown again it picks up as one loop, from where he is (no catch-up jump), at the same frame
rate as before. Hidden by the shell, his page timers stop too (the notch halo's convoCheck, the click region's
sendRegion(false), the 15 s /api/state poll) and come back once each, at the same rates, when he's shown.
The real page in headless Chromium (Playwright under node), from file://: its fetches just fail.

    python3 -m unittest discover -s tests
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "app" / "static" / "index.html"


def node_path():
    """Where a global playwright lives (npm's global root, or the node-tools one), or None."""
    if not shutil.which("node"):
        return None
    roots = [os.environ.get("NODE_PATH", ""), "/opt/node-tools/node_modules"]
    try:
        roots.insert(0, subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, timeout=20).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    roots = [r for r in os.pathsep.join(roots).split(os.pathsep) if r and (Path(r) / "playwright").is_dir()]
    return os.pathsep.join(roots) or None


NODE_PATH = node_path()

# the page's main loop is the rAF callback named frame that works out rawDt; each one asked for (and not yet run or
# cancelled) is pending; each run is logged with its ts and the frame clock (lastTs) it found
INIT = r"""(() => {
  const raf = window.requestAnimationFrame.bind(window), caf = window.cancelAnimationFrame.bind(window);
  const L = window.__loop = {pending: new Set(), maxPending: 0, calls: 0, log: []};
  const isMain = cb => typeof cb === 'function' && cb.name === 'frame' && /rawDt/.test(String(cb));
  window.requestAnimationFrame = cb => {
    if (!isMain(cb)) return raf(cb);
    const id = raf(ts => { L.pending.delete(id); L.calls++;
      let before = null; try { before = lastTs; } catch (e) {}
      L.log.push([ts, before]); if (L.log.length > 400) L.log.shift();
      return cb(ts); });
    L.pending.add(id); L.maxPending = Math.max(L.maxPending, L.pending.size); return id; };
  window.cancelAnimationFrame = id => { L.pending.delete(id); return caf(id); };
  window.__vis = v => { for (const [k, val] of [['visibilityState', v], ['hidden', v === 'hidden']])
      Object.defineProperty(document, k, {configurable: true, get: () => val});
    document.dispatchEvent(new Event('visibilitychange')); };
  window.EventSource = class { constructor() {} close() {} addEventListener() {} };   // (no server)
})();"""

SCRIPT = r"""const { chromium } = require('playwright');
(async () => {
  const out = {errors: []}, b = await chromium.launch();
  try {
    const p = await b.newPage({viewport: {width: 340, height: 460}});
    p.on('pageerror', e => out.errors.push(String(e && e.message || e)));
    await p.addInitScript(INIT);
    await p.goto('file://' + PAGE);
    await p.waitForFunction(() => typeof window.voidHidden === 'function' && window.__loop.calls > 5, null, {timeout: 10000});
    const S = () => p.evaluate(() => ({calls: __loop.calls, pending: __loop.pending.size, max: __loop.maxPending}));
    const reset = () => p.evaluate(() => { __loop.maxPending = __loop.pending.size; });
    const wait = ms => p.waitForTimeout(ms);
    const span = async ms => { await reset(); const a = await S(), t0 = Date.now(); await wait(ms); const z = await S();
      return {n: z.calls - a.calls, secs: (Date.now() - t0) / 1000, max: z.max, pending: z.pending}; };

    out.shown = await span(1000);                                       // 1: shown
    await p.evaluate(() => voidHidden()); await wait(100);              // 2: hidden by the shell
    out.hidden = await span(500);
    await p.evaluate(() => { window.__mark = __loop.calls; voidShown(); });   // 3/4: shown again
    out.resumed = await span(700);
    out.first = await p.evaluate(() => __loop.log[__loop.log.length - (__loop.calls - window.__mark)] || null);
    await p.evaluate(() => { voidShown(); voidShown(); voidHidden(); voidShown(); voidHidden(); voidShown(); });
    await wait(50); out.rapid = await span(1000);
    await p.evaluate(() => __vis('hidden')); await wait(100);          // 5: the page hidden
    out.visHidden = await span(500);
    await p.evaluate(() => __vis('visible')); await wait(100);
    out.visShown = await span(500);
    await p.evaluate(() => { voidHidden(); __vis('hidden'); __vis('visible'); }); await wait(100);
    out.visWhileHidden = await span(500);                               // (still hidden by the shell: stays off)
    await p.evaluate(() => voidShown()); await wait(100);
    out.visEnd = await span(500);
  } catch (e) { out.fatal = String(e && e.stack || e); }
  await b.close(); process.stdout.write(JSON.stringify(out));
})();"""


def run_page(script, init):
    """Run one node+playwright script against the page; its JSON output (or skip the class if it can't run)."""
    with tempfile.TemporaryDirectory() as d:
        js = Path(d) / "idle.js"
        js.write_text(script.replace("INIT", json.dumps(init)).replace("PAGE", json.dumps(str(PAGE))))
        env = dict(os.environ, NODE_PATH=NODE_PATH)
        env.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
        r = subprocess.run(["node", str(js)], capture_output=True, text=True, timeout=60, env=env)
    try:
        out = json.loads(r.stdout)
    except ValueError:
        raise unittest.SkipTest(f"playwright didn't run: {r.stderr[-400:]}")
    if "fatal" in out and "shown" not in out:
        raise unittest.SkipTest(f"browser didn't start: {out['fatal'][:400]}")
    return out


@unittest.skipUnless(NODE_PATH, "node with a global playwright only")
class Idle(unittest.TestCase):
    """One page, one run through shown → hidden → shown → rapid toggles → page hidden/visible; each test reads its part."""
    @classmethod
    def setUpClass(cls):
        cls.r = run_page(SCRIPT, INIT)

    def rate(self, k):
        s = self.r[k]
        return s["n"] / s["secs"]

    def test_page_loads_without_fatal_errors(self):
        self.assertNotIn("fatal", self.r, self.r.get("fatal"))
        self.assertEqual(self.r["errors"], [])

    def test_shown_runs_one_loop_a_frame(self):
        self.assertGreater(self.rate("shown"), 30)                       # (headless: ~60 a second)
        self.assertLess(self.rate("shown"), 75)                          # (one frame() per display frame, no more)
        self.assertEqual(self.r["shown"]["max"], 1)

    def test_hidden_stops_the_loop(self):
        self.assertEqual(self.r["hidden"]["n"], 0)
        self.assertEqual(self.r["hidden"]["pending"], 0)

    def test_shown_again_resumes_one_loop(self):
        self.assertEqual(self.r["hidden"]["n"], 0)                       # (it really stopped first)
        self.assertGreater(self.rate("resumed"), 30)
        self.assertEqual(self.r["resumed"]["max"], 1)

    def test_rapid_toggles_leave_one_loop(self):
        self.assertEqual(self.r["rapid"]["max"], 1)
        self.assertLess(self.rate("rapid"), self.rate("shown") * 1.4)    # (two chains: twice the frames)
        self.assertGreater(self.rate("rapid"), self.rate("shown") * 0.6)

    def test_resume_has_no_catch_up_jump(self):
        self.assertEqual(self.r["hidden"]["n"], 0)                       # (no frames while hidden: a real gap to jump)
        ts, before = self.r["first"]
        self.assertTrue(not before or ts - before < 40,                 # (frame clock reset: its first dt ~0)
                        f"first frame after resume at {ts}, frame clock still {before}")

    def test_page_hidden_stops_and_visible_resumes(self):
        self.assertEqual(self.r["visHidden"]["n"], 0)
        self.assertGreater(self.rate("visShown"), 30)
        self.assertEqual(self.r["visShown"]["max"], 1)

    def test_page_visible_while_shell_hid_him_stays_stopped(self):
        self.assertEqual(self.r["visWhileHidden"]["n"], 0)
        self.assertGreater(self.rate("visEnd"), 30)
        self.assertEqual(self.r["visEnd"]["max"], 1)


# the page's own timers, told apart by their callback's source (setInterval or a setTimeout chain, either way):
# convo = the notch halo's convoCheck, region = sendRegion(false), poll = the `if(!busy) refresh()` /api/state poll.
# Each one's callback runs are counted; each one still to come (an interval not cleared, a timeout not yet run or
# cleared) is active, with its delay
TINIT = r"""(() => {
  const si = window.setInterval.bind(window), ci = window.clearInterval.bind(window);
  const st = window.setTimeout.bind(window), ct = window.clearTimeout.bind(window);
  const T = window.__t = {runs: {convo: 0, region: 0, poll: 0}, active: new Map(), api: 0};
  const kind = fn => { if (typeof fn !== 'function') return null; const s = String(fn);
    if (fn.name === 'convoCheck' || (/\bconvoCheck\b/.test(s) && s.length < 400)) return 'convo';
    if (/sendRegion\(false\)/.test(s) && s.length < 400) return 'region';
    if (/refresh\(\)/.test(s) && /!busy/.test(s) && s.length < 400) return 'poll';
    return null; };
  window.setInterval = (fn, ms, ...a) => { const k = kind(fn); if (!k) return si(fn, ms, ...a);
    const id = si(function () { T.runs[k]++; return fn.apply(this, arguments); }, ms, ...a);
    T.active.set(id, [k, ms]); return id; };
  window.setTimeout = (fn, ms, ...a) => { const k = kind(fn); if (!k) return st(fn, ms, ...a);
    const id = st(function () { T.active.delete(id); T.runs[k]++; return fn.apply(this, arguments); }, ms, ...a);
    T.active.set(id, [k, ms]); return id; };
  window.clearInterval = id => { T.active.delete(id); return ci(id); };
  window.clearTimeout = id => { T.active.delete(id); return ct(id); };
  const f = window.fetch && window.fetch.bind(window);
  window.fetch = (u, ...a) => { if (String(u).includes('/api/state')) T.api++;
    return f ? f(u, ...a) : Promise.reject(new Error('no fetch')); };
  window.EventSource = class { constructor() {} close() {} addEventListener() {} };   // (no server)
})();"""

TSCRIPT = r"""const { chromium } = require('playwright');
(async () => {
  const out = {errors: []}, b = await chromium.launch();
  try {
    const p = await b.newPage({viewport: {width: 340, height: 460}});
    p.on('pageerror', e => out.errors.push(String(e && e.message || e)));
    await p.addInitScript(INIT);
    await p.goto('file://' + PAGE);
    await p.waitForFunction(() => typeof window.voidShown === 'function' && __t.runs.convo > 5 && __t.runs.region > 0,
                            null, {timeout: 10000});
    const S = () => p.evaluate(() => { const act = {convo: [], region: [], poll: []};
      for (const [k, ms] of __t.active.values()) act[k].push(ms);
      return {runs: Object.assign({}, __t.runs), act}; });
    const wait = ms => p.waitForTimeout(ms);
    const span = async ms => { const a = await S(), t0 = Date.now(); await wait(ms); const z = await S();
      const n = {}; for (const k in z.runs) n[k] = z.runs[k] - a.runs[k];
      return {n, secs: (Date.now() - t0) / 1000, act: z.act}; };

    out.shown = await span(1000);                                       // shown
    await p.evaluate(() => voidHidden()); await wait(150);              // hidden by the shell
    out.hidden = await span(1000);
    await p.evaluate(() => voidShown());                                // shown again
    out.resumed = await span(1000);
    await p.evaluate(() => { voidHidden(); voidShown(); voidShown(); voidHidden(); voidShown(); voidHidden(); voidShown(); });
    await wait(50); out.rapid = await span(1000);                       // rapid toggles: still one of each
    await p.evaluate(() => { voidHidden(); voidHidden(); }); await wait(150);
    out.hiddenTwice = await span(600);                                  // hidden twice over: still off
  } catch (e) { out.fatal = String(e && e.stack || e); }
  await b.close(); process.stdout.write(JSON.stringify(out));
})();"""


@unittest.skipUnless(NODE_PATH, "node with a global playwright only")
class IdleTimers(unittest.TestCase):
    """One page, shown → hidden → shown → rapid toggles → hidden twice; each test reads its part."""
    @classmethod
    def setUpClass(cls):
        cls.r = run_page(TSCRIPT, TINIT)

    def rate(self, part, k):
        s = self.r[part]
        return s["n"][k] / s["secs"]

    def assertRunning(self, part):
        """convoCheck ~20 a second, sendRegion(false) ~2.5, and one 15 s poll (one of each timer, no doubles)."""
        s = self.r[part]
        self.assertGreater(self.rate(part, "convo"), 12, s)
        self.assertLess(self.rate(part, "convo"), 26, s)                  # (two of them: ~40 a second)
        self.assertGreaterEqual(s["n"]["region"], 2, s)
        self.assertLessEqual(s["n"]["region"], 4, s)                      # (two of them: ~5 a second)
        self.assertLessEqual(len(s["act"]["convo"]), 1, s)
        self.assertLessEqual(len(s["act"]["region"]), 1, s)
        self.assertEqual(s["act"]["poll"], [15000], s)

    def assertStopped(self, part):
        s = self.r[part]
        self.assertEqual(s["n"]["convo"], 0, s)
        self.assertEqual(s["n"]["region"], 0, s)
        self.assertEqual(s["n"]["poll"], 0, s)
        self.assertEqual(s["act"]["poll"], [], s)                         # (no /api/state poll waiting to go off)
        self.assertEqual(s["act"]["convo"], [], s)
        self.assertEqual(s["act"]["region"], [], s)

    def test_timers_page_loads_without_fatal_errors(self):
        self.assertNotIn("fatal", self.r, self.r.get("fatal"))
        self.assertEqual(self.r["errors"], [])

    def test_shown_runs_each_timer_once(self):
        self.assertRunning("shown")

    def test_hidden_stops_the_timers(self):
        self.assertStopped("hidden")

    def test_shown_again_resumes_the_timers(self):
        self.assertEqual(self.r["hidden"]["n"]["convo"], 0)               # (they really stopped first)
        self.assertRunning("resumed")

    def test_rapid_toggles_leave_one_of_each(self):
        self.assertRunning("rapid")

    def test_hidden_twice_stays_stopped(self):
        self.assertStopped("hiddenTwice")


if __name__ == "__main__":
    unittest.main()
