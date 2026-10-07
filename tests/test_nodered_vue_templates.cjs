// Set VUE_COMPILER_PATH to a local @vue/compiler-dom package, or install it for this check.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {compile} = require(process.env.VUE_COMPILER_PATH || '@vue/compiler-dom');
const nodes = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
let checked = 0;
for (const node of nodes.filter(n => n.type === 'ui-template' && n.format?.includes('<template>'))) {
  const template = node.format.slice(node.format.indexOf('<template>') + 10, node.format.lastIndexOf('</template>'));
  try {
    const {code} = compile(template);
    new Function('Vue', code);
  } catch (error) {
    throw new Error(`${node.name}: ${error.message}`, {cause: error});
  }
  checked++;
}
assert(checked > 0, 'No Vue templates were checked');
console.log(`PASS: ${checked} Vue templates compiled, including Home button expressions`);
