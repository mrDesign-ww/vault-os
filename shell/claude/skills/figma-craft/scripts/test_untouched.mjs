// Offline mock test for untouched.js: node test_untouched.mjs. No Figma calls.
import fs from 'node:fs';
import assert from 'node:assert';
const src = fs.readFileSync(new URL('./untouched.js', import.meta.url), 'utf8');
const AF = Object.getPrototypeOf(async function () {}).constructor;
const mk = () => {
  const row = { id: '1:1', name: 'row', type: 'FRAME', x: 0, y: 0, width: 800, height: 300, visible: true, opacity: 1, fills: [{ type: 'SOLID', color: { r: 1, g: 0, b: 0 } }], layoutMode: 'HORIZONTAL', itemSpacing: 8, paddingTop: 16, paddingRight: 16, paddingBottom: 16, paddingLeft: 16, layoutSizingHorizontal: 'FIXED', layoutSizingVertical: 'FIXED' };
  const title = { id: '1:2', name: 'title', type: 'TEXT', x: 10, y: 10, width: 100, height: 20, visible: true, characters: 'Hello', fontSize: 24, fontName: { family: 'Inter', style: 'Bold' } };
  const card = { id: '1:3', name: 'card', type: 'FRAME', x: 120, y: 10, width: 200, height: 200, visible: true, fills: [], layoutMode: 'VERTICAL', paddingTop: 16 };
  const btn = { id: '1:4', name: 'btn', type: 'INSTANCE', x: 340, y: 10, width: 80, height: 40, visible: true, componentProperties: { Variant: { value: 'a' } }, _mc: { id: 'c:1' }, getMainComponentAsync() { return Promise.resolve(this._mc); } };
  row.children = [title, card, btn];
  const all = [row, title, card, btn];
  for (const c of row.children) c.parent = row;
  row.findAll = f => row.children.filter(f);
  row.parent = { id: 'p', type: 'PAGE' };
  return { row, title, card, btn, figma: { getNodeByIdAsync: async id => ({ '1:1': row }[id]), setCurrentPageAsync: async () => {} } };
};
const run = async (figma, mode, baseline) => {
  const code = src.replace("PROTECTED_IDS: [],", "PROTECTED_IDS: ['1:1'],").replace("MODE: 'snapshot',", `MODE: '${mode}',`).replace("BASELINE: null,", `BASELINE: ${JSON.stringify(baseline || null)},`);
  return new AF('figma', code)(figma);
};
const cases = {
  'no edit (expect same)': [() => {}, 0],
  'move': [m => { m.card.x += 300; }, 1],
  'resize': [m => { m.card.width = 40; }, 1],
  'hide': [m => { m.card.visible = false; }, 1],
  'fill': [m => { m.card.fills = [{ type: 'SOLID', color: { r: 0, g: 1, b: 0 } }]; }, 1],
  'fontSize': [m => { m.title.fontSize = 8; }, 1],
  'text': [m => { m.title.characters = 'Bye'; }, 1],
  'padding': [m => { m.card.paddingTop = 0; }, 1],
  'variant swap (componentProperties)': [m => { m.btn.componentProperties = { Variant: { value: 'b' } }; }, 1],
  'variant swap (mainComponent)': [m => { m.btn._mc = { id: 'c:2' }; }, 1],
  'child order': [m => { m.row.children.reverse(); }, 1],
  'add descendant': [m => { const n = { id: '1:9', name: 'x', type: 'FRAME', x: 0, y: 0, width: 1, height: 1, visible: true, parent: m.row }; m.row.children.push(n); }, 1],
  'remove descendant': [m => { m.row.children.pop(); }, 1],
  'sub-0.1 jitter ignored': [m => { m.card.x += 0.04; }, 0],
  'root own width': [m => { m.row.width = 801; }, 1],
};
let n = 0;
for (const [name, [edit, want]] of Object.entries(cases)) {
  const m = mk();
  const snap = (await run(m.figma, 'snapshot')).snapshot;
  edit(m);
  const d = await run(m.figma, 'diff', snap);
  assert.strictEqual(d.changedOrMissing, want, name + ' ' + JSON.stringify(d.diff));
  n++;
}
await assert.rejects(() => run(mk().figma, 'snapshot').then(() => new AF('figma', src)(mk().figma)), /PROTECTED_IDS empty/);
console.log('OK', n + 1, 'checks');
