import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';

const webRoot = path.resolve(new URL('..', import.meta.url).pathname);
const sourceRoot = path.join(webRoot, 'src');

function walk(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const fullPath = path.join(directory, entry.name);
    if (entry.isDirectory()) return entry.name === 'i18n' ? [] : walk(fullPath);
    return /\.(ts|tsx)$/.test(entry.name) ? [fullPath] : [];
  });
}

function collectLiteralKeys(node, keys) {
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
    keys.add(node.text);
    return;
  }
  if (ts.isConditionalExpression(node)) {
    collectLiteralKeys(node.whenTrue, keys);
    collectLiteralKeys(node.whenFalse, keys);
  }
}

const explicitUiKeys = new Set();
const i18nConsumerFiles = [];
for (const file of walk(sourceRoot)) {
  const source = fs.readFileSync(file, 'utf8');
  if (source.includes('useI18n(')) i18nConsumerFiles.push(path.relative(sourceRoot, file));
  const sourceFile = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, file.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  function visit(node) {
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === 't' && node.arguments[0]) {
      collectLiteralKeys(node.arguments[0], explicitUiKeys);
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
}

const english = JSON.parse(fs.readFileSync(path.join(sourceRoot, 'i18n/messages.en.json'), 'utf8'));
const japanese = JSON.parse(fs.readFileSync(path.join(sourceRoot, 'i18n/messages.ja.json'), 'utf8'));
const korean = JSON.parse(fs.readFileSync(path.join(sourceRoot, 'i18n/messages.ko.json'), 'utf8'));
const englishKeys = Object.keys(english).sort();
const japaneseKeys = Object.keys(japanese).sort();

assert.deepEqual(englishKeys, japaneseKeys, 'English and Japanese catalogs must use the same explicit UI keys');
assert.ok(englishKeys.length >= 900, `Expected the migrated UI catalog, received only ${englishKeys.length} entries`);
assert.deepEqual([...explicitUiKeys].filter((key) => !english[key]), [], 'Every explicit t() key must have an English translation');
assert.deepEqual([...explicitUiKeys].filter((key) => !japanese[key]), [], 'Every explicit t() key must have a Japanese translation');
assert.deepEqual(englishKeys.filter((key) => typeof english[key] !== 'string' || !english[key].trim()), [], 'English translations must not be empty');
assert.deepEqual(japaneseKeys.filter((key) => typeof japanese[key] !== 'string' || !japanese[key].trim()), [], 'Japanese translations must not be empty');
assert.deepEqual(Object.keys(korean).filter((key) => !english[key]), [], 'Korean copy overrides must stay inside the explicit UI catalog boundary');
assert.deepEqual(Object.keys(korean).filter((key) => typeof korean[key] !== 'string' || !korean[key].trim()), [], 'Korean copy overrides must not be empty');
assert.equal(english['압축 모델'], 'Summary model');
assert.equal(english['페이지'], 'Items per page');
assert.equal(japanese['압축 모델'], '要約モデル');
assert.equal(japanese['페이지'], '表示件数');
assert.equal(korean['압축 모델'], '요약 모델');
assert.equal(korean['기본값 저장'], '변경사항 저장');

const provider = fs.readFileSync(path.join(sourceRoot, 'i18n/I18nProvider.tsx'), 'utf8');
const i18nCore = fs.readFileSync(path.join(sourceRoot, 'i18n/core.ts'), 'utf8');
const appProviders = fs.readFileSync(path.join(sourceRoot, 'app/AppProviders.tsx'), 'utf8');
const settings = fs.readFileSync(path.join(sourceRoot, 'views/Settings/index.tsx'), 'utf8');

assert.match(i18nCore, /SUPPORTED_LOCALES = \['ko', 'en', 'ja'\]/);
assert.match(i18nCore, /LOCALE_STORAGE_KEY = 'lorechat-language'/);
assert.match(i18nCore, /import koreanMessages from '.\/messages\.ko\.json'/);
assert.match(i18nCore, /formatUiCount/);
assert.match(i18nCore, /export type Translator = \(key: UiMessageKey\) => string/);
assert.match(provider, /document\.documentElement\.lang = locale/);
assert.doesNotMatch(provider, /TreeWalker|MutationObserver|querySelectorAll|textContent\s*=|setAttribute\s*\(/, 'The provider must never traverse or rewrite rendered content');
assert.doesNotMatch(i18nCore, /split\(|replaceAll\(|\.replace\(|\.join\(/, 'UI translation must not perform fragment replacement');
assert.match(appProviders, /<I18nProvider>/);
const requiredConsumers = [
  'components/organisms/AppShell.tsx',
  'components/organisms/Drawer.tsx',
  'views/ConversationDetail/index.tsx',
  'views/ModelSettings/index.tsx',
  'views/Settings/index.tsx',
];
assert.deepEqual(requiredConsumers.filter((file) => !i18nConsumerFiles.includes(file)), [], 'Primary application chrome must consume explicit i18n context');
assert.match(settings, /value="ko"/);
assert.match(settings, /value="en"/);
assert.match(settings, /value="ja"/);
assert.match(settings, /setLocale\(event\.target\.value as AppLocale\)/);
assert.doesNotMatch(settings, /CardDescription|유저가 직접 조절하는 개인 설정 메뉴입니다|화면에 표시할 언어를 선택합니다/);

const runtimeSettings = fs.readFileSync(path.join(sourceRoot, 'components/organisms/RuntimeSettings.tsx'), 'utf8');
assert.doesNotMatch(runtimeSettings, /CardDescription|대화 길이는 프롬프트 목표치|켜짐 · 메시지에 첨부된 사진|꺼짐 · 채팅은 유지하고 사진만 숨깁니다/);

for (const sample of ['배틀', '리그 세계관 · 리그 세계관 배틀', '스포트라이트 뒤의 비밀연애', '2명']) {
  assert.doesNotMatch(provider, new RegExp(sample.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), 'User/DB samples must not enter the provider');
}

console.log(`i18n boundary contract ok · ${explicitUiKeys.size} explicit keys · ${i18nConsumerFiles.length} consumer files`);
