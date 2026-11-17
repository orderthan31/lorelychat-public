import { readFileSync, readdirSync, statSync } from 'node:fs';
import assert from 'node:assert/strict';
import { hasActiveDecisionModel, normalizeCompressionStrategy, normalizeRuntimeSetting, normalizeRuntimeSettingUpdatePayload } from '../src/utils/runtimeSettings.ts';

assert.equal(normalizeCompressionStrategy(undefined), 'quality');
assert.equal(normalizeCompressionStrategy('legacy-unknown'), 'quality');
assert.equal(normalizeCompressionStrategy('fast'), 'fast');
assert.deepEqual(normalizeRuntimeSetting({ scope: 'conversation' }), { scope: 'conversation', compression_strategy: 'quality' });
const runtimePayloadDefaults = { response_length_preset: 'medium' };
assert.equal(hasActiveDecisionModel(), false);
assert.equal(hasActiveDecisionModel({ decision_model_option_key: 'inactive', decision_options: [{ key: 'active' }] }), false);
assert.equal(hasActiveDecisionModel({ decision_model_option_key: 'active', decision_options: [{ key: 'active' }] }), true);
assert.equal(hasActiveDecisionModel({ decision_model_option_key: 'active' }), false);
assert.equal(normalizeRuntimeSettingUpdatePayload({ decision_model_option_key: 'active' }, runtimePayloadDefaults).decision_enabled, false);
assert.equal(normalizeRuntimeSettingUpdatePayload({ decision_enabled: false, decision_model_option_key: 'active' }, runtimePayloadDefaults).decision_model_option_key, 'active');
assert.equal(normalizeRuntimeSettingUpdatePayload({ decision_enabled: false, decision_model_option_key: null }, runtimePayloadDefaults).decision_model_option_key, null);
const fullRuntimeSetting = {
  model_key: 'chat-model',
  fallback_model_key: 'chat-fallback',
  compression_model_key: 'compression-model',
  compression_fallback_model_key: 'compression-fallback',
  default_tts_model_option_key: 'tts-model',
  decision_enabled: true,
  decision_model_option_key: 'decision-model',
  safety_preset: 'high',
  response_length_preset: 'long',
  compression_strategy: 'fast',
};
assert.deepEqual(normalizeRuntimeSettingUpdatePayload(fullRuntimeSetting, runtimePayloadDefaults), {
  model_key: 'chat-model',
  fallback_model_key: 'chat-fallback',
  compression_model_key: 'compression-model',
  compression_fallback_model_key: 'compression-fallback',
  default_tts_model_option_key: 'tts-model',
  decision_enabled: true,
  decision_model_option_key: 'decision-model',
  safety_preset: 'high',
  response_length_preset: 'long',
  compression_strategy: 'fast',
});
assert.deepEqual(normalizeRuntimeSettingUpdatePayload({}, runtimePayloadDefaults), {
  model_key: null,
  fallback_model_key: null,
  compression_model_key: null,
  compression_fallback_model_key: null,
  default_tts_model_option_key: null,
  decision_enabled: false,
  decision_model_option_key: null,
  safety_preset: 'medium',
  response_length_preset: 'medium',
  compression_strategy: 'quality',
});
assert.equal(normalizeRuntimeSettingUpdatePayload({ compression_strategy: 'quality' }, runtimePayloadDefaults).compression_strategy, 'quality');
assert.equal(normalizeRuntimeSettingUpdatePayload({ compression_strategy: 'invalid' }, runtimePayloadDefaults).compression_strategy, 'quality');

// These negative guards protect security/product boundaries, not file placement.
function readSourceTree(url) {
  if (statSync(url).isFile()) return /\.(ts|tsx)$/.test(url.pathname) ? readFileSync(url, 'utf8') : '';
  return readdirSync(url).map(name => readSourceTree(new URL(name + (statSync(new URL(name, url)).isDirectory() ? '/' : ''), url))).join('\n');
}
const source = readSourceTree(new URL('../src/', import.meta.url));
const boundary = readFileSync(new URL('../src/app/AppErrorBoundary.tsx', import.meta.url), 'utf8');
assert.doesNotMatch(boundary, /message_content|request_payload|response_body/, 'diagnostics must not persist private request/response bodies');
assert.doesNotMatch(source, /rehypeRaw/, 'authored Markdown must not enable raw HTML execution');
assert.doesNotMatch(source, /views\/Operations|views\/Uiux|operations: '\/operations'|uiux: '\/uiux'/, 'retired admin/example surfaces remain unreachable');
assert.doesNotMatch(source, /auto-turns\/stream/, 'retired auto-turn transport must not bypass durable generation jobs');
assert.doesNotMatch(source, /chatCommands: \(\) => api\('\/chat-commands\?include_disabled=true'\)/, 'runtime command selection must not include disabled commands');
console.log('PASS runtime normalization and negative privacy/Markdown/retired-transport boundaries');
