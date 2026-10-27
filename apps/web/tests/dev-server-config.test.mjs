import assert from 'node:assert/strict';

const keys = ['LORECHAT_DEV_PORT', 'LORECHAT_DEV_HOST', 'LORECHAT_DEV_API_TARGET'];
const original = Object.fromEntries(keys.map((key) => [key, process.env[key]]));
let sequence = 0;
async function load(values = {}) {
  for (const key of keys) {
    if (values[key] === undefined) delete process.env[key];
    else process.env[key] = values[key];
  }
  return (await import(`../vite.config.js?configuration-test=${sequence++}`)).default;
}
try {
  const defaults = await load();
  assert.equal(defaults.server.port, 5173);
  assert.equal(defaults.server.host, '0.0.0.0');
  assert.equal(defaults.server.strictPort, true);
  assert.equal(defaults.server.proxy['/api'].target, 'http://127.0.0.1:8123');
  assert.equal(defaults.server.proxy['/uploads'].target, 'http://127.0.0.1:8123');

  const isolated = await load({
    LORECHAT_DEV_PORT: '5174',
    LORECHAT_DEV_HOST: '127.0.0.1',
    LORECHAT_DEV_API_TARGET: 'http://127.0.0.1:8124',
  });
  assert.equal(isolated.server.port, 5174);
  assert.equal(isolated.server.host, '127.0.0.1');
  assert.equal(isolated.server.strictPort, true);
  assert.equal(isolated.server.proxy['/api'].target, 'http://127.0.0.1:8124');
  assert.equal(isolated.server.proxy['/uploads'].target, 'http://127.0.0.1:8124');
  assert.equal(isolated.server.proxy['/api'].rewrite('/api/ready'), '/ready');
  assert.equal(isolated.preview.port, 5174);
  for (const port of ['not-a-port', '0', '65536', '5174.5']) {
    await assert.rejects(load({ LORECHAT_DEV_PORT: port }), /LORECHAT_DEV_PORT/);
  }
  console.log('Development server configuration tests passed');
} finally {
  for (const key of keys) {
    if (original[key] === undefined) delete process.env[key];
    else process.env[key] = original[key];
  }
}
