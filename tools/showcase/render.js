// Renders the README showcase images for ha-food-diary from the raw simulator screenshots.
// Usage: node render.js <repo root>
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const repo = process.argv[2];
const raw = (name) => 'file://' + path.join(repo, 'docs/screenshots/raw', name);
const out = path.join(repo, 'docs/images');
fs.mkdirSync(out, { recursive: true });

const C = {
  parchment: '#F1EAD8', sand: '#D5C7AD', olive: '#BEC5A4', sage: '#8A8E75',
  bark: '#68604D', olivewood: '#2D2F22', ring: '#6A7152', clay: '#A4553A',
};

const base = `
<style>
@font-face { font-family: NY; src: url(file:///System/Library/Fonts/NewYork.ttf); }
@font-face { font-family: NYI; src: url(file:///System/Library/Fonts/NewYorkItalic.ttf); }
@font-face { font-family: SF; src: url(file:///System/Library/Fonts/SFNS.ttf); }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: SF; color: ${C.olivewood}; background: ${C.parchment}; -webkit-font-smoothing: antialiased; }
.canvas { position: relative; overflow: hidden; background:
  radial-gradient(1200px 600px at 85% 20%, #F7F2E4 0%, transparent 60%),
  radial-gradient(900px 700px at 0% 100%, #E6DDC6 0%, transparent 70%), ${C.parchment}; }
.phone { position: relative; border-radius: 64px; padding: 13px; background: linear-gradient(145deg, #3a3c2e, #1d1e16);
  box-shadow: 0 2px 0 1px #50523f inset, 0 40px 80px -20px rgba(45,47,34,.45), 0 12px 24px -8px rgba(45,47,34,.3); }
.phone img { display: block; width: 100%; border-radius: 52px; }
.phone::after { content: ''; position: absolute; top: 24px; left: 50%; transform: translateX(-50%);
  width: 32%; height: 34px; border-radius: 20px; background: #0d0d0a; }
h1 { font-family: NY; font-weight: 600; letter-spacing: -0.02em; }
.eyebrow { text-transform: uppercase; letter-spacing: .16em; font-size: 15px; font-weight: 600; color: ${C.sage}; }
.chip { display: inline-flex; align-items: center; gap: 8px; padding: 10px 16px; border-radius: 999px;
  background: rgba(190,197,164,.45); color: ${C.olivewood}; font-size: 17px; font-weight: 500; }
.sprig { position: absolute; opacity: .22; }
</style>`;

// A hand-drawn-ish olive sprig: a curved stem with paired leaves.
function sprig({ x, y, w, rot = 0, color = C.sage, opacity = 0.22 }) {
  const leaves = [];
  for (let i = 0; i < 7; i++) {
    const t = 20 + i * 38;
    leaves.push(`<ellipse cx="${t}" cy="${-14}" rx="16" ry="6" transform="rotate(-28 ${t} -14)" />`);
    leaves.push(`<ellipse cx="${t + 14}" cy="${14}" rx="16" ry="6" transform="rotate(28 ${t + 14} 14)" />`);
  }
  return `<svg class="sprig" style="left:${x}px;top:${y}px;width:${w}px;opacity:${opacity};transform:rotate(${rot}deg)"
    viewBox="-10 -40 300 80" fill="${color}" stroke="${color}">
    <path d="M0 0 C 80 -10, 180 10, 290 -4" fill="none" stroke-width="3" stroke-linecap="round"/>${leaves.join('')}</svg>`;
}

const phone = (img, w, style = '') => `<div class="phone" style="width:${w}px;${style}"><img src="${raw(img)}"></div>`;

const hero = () => `${base}
<div class="canvas" style="width:1600px;height:860px">
  ${sprig({ x: -40, y: 690, w: 520, rot: -8 })}
  ${sprig({ x: 1180, y: 40, w: 460, rot: 168, opacity: .16 })}
  <div style="position:absolute;left:96px;top:150px;width:620px">
    <div class="eyebrow">Home Assistant · iPhone · Apple Health</div>
    <h1 style="font-size:76px;line-height:1.02;margin:22px 0 26px">Food Diary,<br>kept at home.</h1>
    <p style="font-size:24px;line-height:1.5;color:${C.bark};max-width:560px">
      A calorie and macro diary that lives in your own Home Assistant, with a native iPhone app that makes logging
      a meal a photo, a barcode or a sentence.</p>
    <div style="display:flex;flex-wrap:wrap;gap:10px;margin-top:36px;max-width:600px">
      <span class="chip">Photo, label &amp; barcode</span><span class="chip">Two-way Apple Health</span>
      <span class="chip">Widgets &amp; controls</span><span class="chip">Voice logging</span>
      <span class="chip">Meal planning</span>
    </div>
  </div>
  ${phone('05-add-check-it.png', 300, 'position:absolute;left:820px;top:170px;transform:rotate(-7deg);opacity:.98')}
  ${phone('10-week-review.png', 300, 'position:absolute;left:1240px;top:170px;transform:rotate(7deg);opacity:.98')}
  ${phone('01-today.png', 340, 'position:absolute;left:1010px;top:80px;z-index:2')}
</div>`;

const strip = (items) => `${base}
<div class="canvas" style="width:1600px;height:930px;padding:70px 60px">
  <div style="display:grid;grid-template-columns:repeat(${items.length},1fr);gap:40px;align-items:start">
    ${items.map(([img, title, text]) => `
      <div style="display:flex;flex-direction:column;align-items:center;text-align:center">
        ${phone(img, items.length > 3 ? 300 : 330)}
        <h1 style="font-size:30px;margin:38px 0 10px">${title}</h1>
        <p style="font-size:19px;line-height:1.45;color:${C.bark};max-width:320px">${text}</p>
      </div>`).join('')}
  </div>
</div>`;

const widget = () => `${base}
<div class="canvas" style="width:1600px;height:640px">
  ${sprig({ x: 40, y: 520, w: 380, rot: -6, opacity: .18 })}
  <div style="position:absolute;left:96px;top:120px;width:600px">
    <div class="eyebrow">Beyond the app</div>
    <h1 style="font-size:46px;line-height:1.1;margin:18px 0 22px">On your Home Screen,<br>in Control Center,<br>and by voice.</h1>
    <p style="font-size:21px;line-height:1.5;color:${C.bark}">Widgets show what's left and your usuals, one tap each.
      Controls open the camera or barcode scanner. The share sheet takes photos and text, and Assist can log
      “two eggs on toast for breakfast”.</p>
  </div>
  <div style="position:absolute;right:110px;top:70px;width:700px;height:500px;border-radius:44px;overflow:hidden;
    box-shadow:0 40px 80px -24px rgba(45,47,34,.45)">
    <img src="${raw('16-widget-home-screen.png')}" style="width:700px;position:absolute;top:-150px;left:0">
  </div>
</div>`;

// Phone ↔ Home Assistant ↔ services, in the app's palette.
const architecture = () => {
  const box = (x, y, w, h, title, lines, fill = '#FBF8EF', stroke = C.sand) => `
    <rect x="${x}" y="${y}" width="${w}" height="${h}" rx="22" fill="${fill}" stroke="${stroke}" stroke-width="2"/>
    <text x="${x + 28}" y="${y + 50}" font-family="NY" font-size="28" font-weight="600" fill="${C.olivewood}">${title}</text>
    ${lines.map((l, i) => `<text x="${x + 28}" y="${y + 88 + i * 30}" font-family="SF" font-size="19" fill="${C.bark}">${l}</text>`).join('')}`;
  const arrow = (x1, y1, x2, y2, label, ly = -12) => `
    <line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${C.ring}" stroke-width="3" marker-end="url(#a)" marker-start="url(#a)"/>
    <text x="${(x1 + x2) / 2 + (x1 === x2 ? 16 : 0)}" y="${(y1 + y2) / 2 + ly + (x1 === x2 ? 6 : 0)}" text-anchor="${x1 === x2 ? 'start' : 'middle'}" font-family="SF" font-size="17" fill="${C.sage}">${label}</text>`;
  return `${base}
<div class="canvas" style="width:1600px;height:760px">
  <svg width="1600" height="760" viewBox="0 0 1600 760">
    <defs><marker id="a" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0 0 L10 5 L0 10 z" fill="${C.ring}"/></marker></defs>
    <text x="80" y="92" font-family="NY" font-size="40" font-weight="600" fill="${C.olivewood}">How it fits together</text>
    <text x="80" y="132" font-family="SF" font-size="20" fill="${C.bark}">The phone keeps no diary of its own. Home Assistant is the source of truth; everything else is optional.</text>
    ${box(80, 200, 400, 260, 'Food for iPhone', ['SwiftUI · iOS 18+', 'Widgets, controls, share sheet', 'Offline queue', 'Signs in with HA OAuth'])}
    ${box(600, 200, 420, 260, 'Home Assistant', ['food_diary integration', '21 services · sensors · goals', 'Webhook · voice intents', 'Planner + numbers check'], '#EEF0E4', C.olive)}
    ${box(1140, 170, 380, 150, 'AI Task', ['Photos, labels, sentences'])}
    ${box(1140, 350, 380, 150, 'Open Food Facts', ['Barcode lookups'])}
    ${box(80, 570, 400, 140, 'Apple Health', ['Food in · activity & sleep out'])}
    ${box(600, 570, 420, 140, 'Your meal plan (optional)', ['Any sensor with the documented shape'])}
    ${arrow(486, 330, 594, 330, 'REST API')}
    ${arrow(1026, 270, 1134, 245, 'ai_task')}
    ${arrow(1026, 400, 1134, 425, 'HTTPS')}
    ${arrow(280, 466, 280, 564, 'HealthKit', 0)}
    ${arrow(810, 466, 810, 564, 'state', 0)}
  </svg>
</div>`;
};

(async () => {
  const browser = await chromium.launch({ args: ['--allow-file-access-from-files'] });
  const page = await browser.newPage({ deviceScaleFactor: 2, viewport: { width: 1600, height: 900 } });
  const shots = {
    'hero.png': hero(),
    'features-logging.png': strip([
      ['03-add-food.png', 'Five ways in', 'A photo of the plate, a barcode, a nutrition label, a sentence, or something you’ve had before.'],
      ['07-add-several-foods.png', 'Check before it lands', '“Porridge, a banana and a flat white” becomes three foods, each with its own portion.'],
      ['06-entry-detail.png', 'Every food, editable', 'Portions, meal and numbers stay yours to change, with Undo everywhere.'],
    ]),
    'features-looking-back.png': strip([
      ['10-week-review.png', 'The week in review', 'Days on target, your average and one highlight, every Sunday afternoon.'],
      ['11-month.png', 'A month at a glance', 'Apple’s activity rings beside each day’s food.'],
      ['08-streak.png', 'A sprig that grows', 'A leaf for each day you land between 75% and 105% of your goal.'],
    ]),
    'widgets.png': widget(),
    'architecture.png': architecture(),
  };
  for (const [name, html] of Object.entries(shots)) {
    const file = path.join(require('os').tmpdir(), name.replace('.png', '.html'));
    fs.writeFileSync(file, html);
    await page.goto('file://' + file);
    await page.waitForLoadState('networkidle');
    await page.evaluate(() => document.fonts.ready);
    const el = await page.$('.canvas');
    await el.screenshot({ path: path.join(out, name) });
    console.log('wrote', name);
  }
  await browser.close();
})();
