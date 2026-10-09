const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..', 'web');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'premium-cyber-v1.css'), 'utf8');
const mobile = fs.readFileSync(path.join(root, 'mobile-responsive-v1.css'), 'utf8');
const classic = fs.readFileSync(path.join(root, 'cyber-classic.css'), 'utf8');

test('Premium Cyber stylesheet loads after stable polish and all feature CSS', () => {
  const styles = [...html.matchAll(/<link\s+rel="stylesheet"\s+href="([^"]+)"/g)]
    .map(x => x[1]);
  assert.equal(styles.at(-1), 'assets/premium-cyber-v1.css?v=1');
  assert.ok(styles.includes('assets/stable-polish-v1.css?v=1'));
  assert.ok(styles.includes('assets/mobile-responsive-v1.css'));
  assert.ok(styles.indexOf('assets/premium-cyber-v1.css?v=1') > styles.indexOf('assets/stable-polish-v1.css?v=1'));
  assert.match(html, /<body class="skin-cyber-classic">/);
});

test('skin uses scoped Midnight tokens and keeps visible warnings distinct', () => {
  for (const declaration of [
    '--premium-base:', '--premium-mint:', '--premium-text:',
    '--premium-line:', '--classic-green:', '--bg:', '--border:',
    'body.skin-cyber-classic .nav-btn',
    'body.skin-cyber-classic .btn-primary',
    'body.skin-cyber-classic .btn.danger',
    'body.skin-cyber-classic .notice.warning',
    'body.skin-cyber-classic .notice.error',
    'body.skin-cyber-classic .panel',
    'body.skin-cyber-classic .nr-dialog',
    'body.skin-cyber-classic .dr-group',
    'body.skin-cyber-classic .nv2-node',
  ]) assert.ok(css.includes(declaration), declaration);
  assert.match(css, /--premium-warning:\s*#ffce7b/);
  assert.match(css, /--premium-danger:\s*#ff8294/);
  assert.notEqual(classic.length, 0);
});

test('CSS is presentation-only: no fetch, storage, control or app mutation', () => {
  for (const blocked of [/\/api\//, /fetch\s*\(/i, /localStorage/, /sessionStorage/,
      /\.innerHTML/, /document\.cookie/, /systemctl/, /content:\s*url/i,
      /pointer-events:\s*none/i]) {
    assert.doesNotMatch(css, blocked);
  }
  assert.doesNotMatch(css, /body\.skin-cyber-classic\s*\{[^}]*display\s*:/);
});

test('mobile navigation, touch inputs and keyboard focus remain accessible', () => {
  assert.match(css, /input:not\(\[type="checkbox"\]\):not\(\[type="radio"\]\)/);
  assert.match(css, /:focus-visible/);
  assert.match(css, /@media\(max-width:760px\)/);
  assert.match(css, /prefers-reduced-motion:reduce/);
  assert.match(css, /body\.skin-cyber-classic\.dark-reduced-motion/);
  assert.match(mobile, /\.cv4-row/);
  assert.match(mobile, /\.overlay,.overlay/); // the mobile bottom-sheet contract stays in its owner file
});

test('Premium layer has balanced CSS blocks and does not own table pagination', () => {
  const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, '');
  let depth = 0;
  for (const ch of withoutComments) {
    if (ch === '{') depth++;
    if (ch === '}') depth--;
    assert.ok(depth >= 0, 'extra closing CSS brace');
  }
  assert.equal(depth, 0, 'unclosed CSS block');
  assert.doesNotMatch(css, /grid-template-areas|grid-template-columns/);
  assert.doesNotMatch(css, /\.cv4-pager/);
});
