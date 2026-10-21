// figma-craft untouched gate. READ ONLY: no writes. Run via use_figma; returns JSON.
// MODE 'snapshot' before the build: save the returned `snapshot`. MODE 'diff' after: paste it into BASELINE.
const CONFIG = {
  PROTECTED_IDS: [],       // owner rows, refs, annotations
  MODE: 'snapshot',        // 'snapshot' | 'diff'
  BASELINE: null,          // JSON returned by snapshot (the `snapshot` object)
};
if (!CONFIG.PROTECTED_IDS.length) throw new Error('PROTECTED_IDS empty');
const t0 = Date.now();
const r = v => (typeof v === 'number' ? Math.round(v * 10) / 10 : v);
const hash = s => { let h = 5381; for (let i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0; return h.toString(16); };
const S = v => { try { const x = JSON.stringify(v, (k, y) => typeof y === 'number' ? r(y) : y); return x === undefined ? String(v) : x; } catch (e) { return String(v); } };
const has = (d, k) => k in d;
const sigOf = async d => [d.id, d.name, d.type, d.parent && d.parent.id, r(d.x), r(d.y), r(d.width), r(d.height),
  has(d, 'rotation') ? r(d.rotation) : '', d.visible, has(d, 'opacity') ? r(d.opacity) : '',
  has(d, 'fills') ? S(d.fills) : '', has(d, 'strokes') ? S(d.strokes) : '', has(d, 'strokeWeight') ? S(d.strokeWeight) : '',
  has(d, 'effects') ? S(d.effects) : '', has(d, 'cornerRadius') ? S(d.cornerRadius) : '', has(d, 'clipsContent') ? d.clipsContent : '',
  has(d, 'layoutMode') ? [d.layoutMode, d.itemSpacing, d.paddingTop, d.paddingRight, d.paddingBottom, d.paddingLeft,
    d.primaryAxisAlignItems, d.counterAxisAlignItems, d.layoutSizingHorizontal, d.layoutSizingVertical].join(',') : '',
  has(d, 'layoutPositioning') ? d.layoutPositioning : '',
  d.type === 'TEXT' ? [d.characters, S(d.fontName), S(d.fontSize), S(d.lineHeight), S(d.letterSpacing), S(d.textStyleId), S(d.fillStyleId)].join(',') : '',
  d.type === 'INSTANCE' ? S(d.componentProperties) + ',' + (((await d.getMainComponentAsync()) || {}).id || '') : ''].join('|');
let pageSet = false;
const snap = {};
for (const id of CONFIG.PROTECTED_IDS) {
  const n = await figma.getNodeByIdAsync(id);
  if (!n) { snap[id] = { missing: true }; continue; }
  if (!pageSet) { let p = n; while (p && p.type !== 'PAGE') p = p.parent; if (p) { await figma.setCurrentPageAsync(p); pageSet = true; } }
  const all = [n, ...('findAll' in n ? n.findAll(() => true) : [])];
  const lines = [];
  for (const d of all) lines.push(await sigOf(d));
  const sig = lines.join('\n');
  snap[id] = { name: n.name, type: n.type, parent: n.parent && n.parent.id, x: r(n.x), y: r(n.y), w: r(n.width), h: r(n.height),
    visible: n.visible, children: 'children' in n ? n.children.length : 0, descendants: all.length - 1, hash: hash(sig) };
}
if (CONFIG.MODE === 'snapshot') return { mode: 'snapshot', snapshot: snap, ms: Date.now() - t0 };
const diff = {}; let bad = 0;
for (const id of new Set([...CONFIG.PROTECTED_IDS, ...Object.keys(CONFIG.BASELINE || {})])) {
  if (!snap[id]) { diff[id] = 'in BASELINE but not in PROTECTED_IDS'; bad++; continue; }
  const a = (CONFIG.BASELINE || {})[id], b = snap[id];
  if (!a) { diff[id] = 'no baseline'; bad++; continue; }
  if (b.missing) { diff[id] = 'missing'; bad++; continue; }
  const ch = Object.keys(a).filter(k => a[k] !== b[k]).map(k => k + ': ' + a[k] + ' -> ' + b[k]);
  if (ch.length) { diff[id] = ch; bad++; } else diff[id] = 'same';
}
return { mode: 'diff', changedOrMissing: bad, diff, ms: Date.now() - t0 };
