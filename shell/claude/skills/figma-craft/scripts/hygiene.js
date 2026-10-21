// figma-craft hygiene gate. READ ONLY: no property writes, no create/remove. Run via use_figma; returns JSON.
// Set CONFIG, then paste the whole file as the `code` argument.
const CONFIG = {
  ROOT_IDS: [],            // frames to audit, e.g. ['12:345']
  TARGET_SECTION_ID: '',   // optional: every root must have this parent
};
const t0 = Date.now();
const first = await figma.getNodeByIdAsync(CONFIG.ROOT_IDS[0]);
let page = first; while (page && page.type !== 'PAGE') page = page.parent;
await figma.setCurrentPageAsync(page);

const DECOR_PREFIX = ['art /', 'decor /', 'fx /', 'bg /'];
const DECOR_NAMES = ['backdrop', 'scrim', 'fade', 'glow', 'ring', 'border', 'plate', 'icon', 'art', 'band'];
const isDecor = (name) => {
  const n = name.toLowerCase().trim();
  return DECOR_PREFIX.some(p => n.startsWith(p)) || DECOR_NAMES.some(d => n === d || n.startsWith(d + ' /'));
};
const GENERIC = /^(Frame|Group|Rectangle|Ellipse|Vector|Line|Polygon|Star|Union|Subtract|Intersect|Exclude|Mask group|Component|Section|Slice)( \d+)?$|^image \d+$/;
const EXPORTNAME = /\.(png|jpe?g|webp|svg|gif|avif)$|^(IMG|DSC|Screenshot|Снимок экрана)[ _-]/i;
const BTNLIKE = /(^|[^a-z])(button|btn|cta|arrow|chevron|pill|chip|tab)s?([^a-z]|$)/i;
const BTNCAMEL = /[a-z0-9](Button|Btn|Cta|Arrow|Chevron|Pill|Chip|Tab)s?([^a-z]|$)/;
const B = ['noLayoutMulti', 'groups', 'bareRects', 'badAbsolute', 'genericNames', 'buttonLikeNotInstance', 'detachedCopies', 'hidden', 'decorWithContent', 'wrongParent'];

// pass 1: component names used by instances in the roots
const compNames = new Set();
for (const id of CONFIG.ROOT_IDS) {
  const r = await figma.getNodeByIdAsync(id);
  for (const mc of await Promise.all(r.findAllWithCriteria({ types: ['INSTANCE'] }).map(i => i.getMainComponentAsync()))) {
    if (mc) compNames.add((mc.parent && mc.parent.type === 'COMPONENT_SET' ? mc.parent : mc).name);
  }
}

const out = { roots: {}, totals: {} };
for (const b of B) out.totals[b] = 0;
for (const id of CONFIG.ROOT_IDS) {
  const root = await figma.getNodeByIdAsync(id);
  const res = { name: root.name, nodes: 0 };
  for (const b of B) res[b] = { count: 0, ids: [] };
  const add = (b, n, extra) => { res[b].count++; out.totals[b]++; if (res[b].ids.length < 25) res[b].ids.push(n.id + ' ' + n.name + (extra ? ' ' + extra : '')); };
  if (CONFIG.TARGET_SECTION_ID && (!root.parent || root.parent.id !== CONFIG.TARGET_SECTION_ID)) add('wrongParent', root, '< ' + (root.parent && root.parent.id));
  const walk = (n, isRoot, inDecor) => {
    res.nodes++;
    const decor = !isRoot && isDecor(n.name);
    if (n.visible === false) add('hidden', n);
    // a decor node is itself exempt from the structural checks; a pure-decor subtree too, but a decor node that holds
    // TEXT or an INSTANCE (title, button) is flagged and its children get every check
    const content = decor && 'findAll' in n && n.findAll(c => c.type === 'TEXT' || c.type === 'INSTANCE').length > 0;
    if (content) add('decorWithContent', n);
    if (!decor && !inDecor) {
      if (n.type === 'FRAME' && n.layoutMode === 'NONE' && n.children.length > 1) add('noLayoutMulti', n);
      if (n.type === 'GROUP') add('groups', n);
      if (n.type === 'RECTANGLE') add('bareRects', n);
      if ('layoutPositioning' in n && n.layoutPositioning === 'ABSOLUTE' && !(n.type === 'INSTANCE' && n.parent && /^cell\b/i.test(n.parent.name))) add('badAbsolute', n); // an instance inside a cell frame is exempt
    }
    if (GENERIC.test(n.name) || EXPORTNAME.test(n.name)) add('genericNames', n);
    if ((n.type === 'FRAME' || n.type === 'GROUP') && (BTNLIKE.test(n.name) || BTNCAMEL.test(n.name))) add('buttonLikeNotInstance', n);
    if (n.type === 'FRAME' && compNames.has(n.name)) add('detachedCopies', n);
    if (n.type !== 'INSTANCE' && 'children' in n) for (const c of n.children) walk(c, false, (inDecor || decor) && !content);
  };
  walk(root, true);
  out.roots[id] = res;
}
out.ms = Date.now() - t0;
return out;
