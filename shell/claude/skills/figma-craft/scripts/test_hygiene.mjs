// Offline mock test for hygiene.js: node test_hygiene.mjs
import fs from 'node:fs';
import assert from 'node:assert';
const src = fs.readFileSync(new URL('./hygiene.js', import.meta.url), 'utf8').replace("ROOT_IDS: [],", "ROOT_IDS: ['r'],");
const AF = Object.getPrototypeOf(async function () {}).constructor;
let id = 0;
const N = (type, name, children, o = {}) => { const n = { id: 'n' + id++, type, name, visible: true, layoutMode: 'NONE', ...o }; if (children) { n.children = children; for (const c of children) c.parent = n; } return n; };
const flat = n => [n, ...(n.children || []).flatMap(flat)];
const attach = n => { n.findAll = f => flat(n).slice(1).filter(f); n.findAllWithCriteria = ({ types }) => flat(n).filter(x => types.includes(x.type)); (n.children || []).forEach(attach); return n; };
const root = attach(N('FRAME', 'card', [
  N('FRAME', 'plate', [N('FRAME', 'button', [N('RECTANGLE', 'Rectangle 666'), N('TEXT', 'label')]), N('GROUP', 'Group 4', [N('VECTOR', 'Vector 2')])]),
  N('FRAME', 'icon button', [N('RECTANGLE', 'x')]),
  N('FRAME', 'ctaButton', []), N('FRAME', 'cta', []),
  N('GROUP', 'Group', []), N('RECTANGLE', 'image 12'), N('RECTANGLE', 'IMG_2041.png'), N('FRAME', 'glow', [N('RECTANGLE', 'r1'), N('RECTANGLE', 'r2')]),
], { layoutMode: 'VERTICAL' }));
root.parent = { type: 'PAGE' };
const figma = { getNodeByIdAsync: async () => root, setCurrentPageAsync: async () => {} };
const t = (await new AF('figma', src)(figma)).totals;
console.log(t);
assert.strictEqual(t.decorWithContent, 1);           // plate holds TEXT
assert.ok(t.buttonLikeNotInstance >= 4);             // button, icon button, ctaButton, cta
assert.ok(t.genericNames >= 6);                      // Rectangle 666, Group 4, Vector 2, Group, image 12, IMG_2041.png
assert.strictEqual(t.groups, 2);                     // Group + Group 4 (plate holds content, so children are checked)
assert.strictEqual(t.noLayoutMulti, 1);              // no-layout button inside the content plate
assert.strictEqual(t.bareRects, 4);                  // + Rectangle 666; glow rects stay exempt
console.log('OK');
