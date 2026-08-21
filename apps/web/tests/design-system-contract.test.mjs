import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const packageJson = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
const tokens = readFileSync(new URL('../src/styles/base/_tokens.scss', import.meta.url), 'utf8');
const atoms = readFileSync(new URL('../src/components/atoms/index.ts', import.meta.url), 'utf8');
const tabs = readFileSync(new URL('../src/components/atoms/tabs.tsx', import.meta.url), 'utf8');
const animatedSettingsMark = readFileSync(new URL('../src/components/atoms/animated-settings-mark.tsx', import.meta.url), 'utf8');
const molecules = readFileSync(new URL('../src/components/molecules/index.ts', import.meta.url), 'utf8');
const resourceCard = readFileSync(new URL('../src/components/molecules/ResourceCard.tsx', import.meta.url), 'utf8');
const resourceListBoundary = readFileSync(new URL('../src/components/molecules/ResourceListBoundary.tsx', import.meta.url), 'utf8');
const skeleton = readFileSync(new URL('../src/components/atoms/skeleton.tsx', import.meta.url), 'utf8');
const formSection = readFileSync(new URL('../src/components/molecules/FormSection.tsx', import.meta.url), 'utf8');
const app = readFileSync(new URL('../src/app/App.tsx', import.meta.url), 'utf8');
const home = readFileSync(new URL('../src/views/Home/index.tsx', import.meta.url), 'utf8');
const suspenseListViews = [
  '../src/views/Characters/index.tsx',
  '../src/views/Conversations/index.tsx',
  '../src/views/Presets/index.tsx',
  '../src/views/WorldSettings/index.tsx',
  '../src/views/ChatCommands/index.tsx',
].map((path) => readFileSync(new URL(path, import.meta.url), 'utf8'));
const keyframes = readFileSync(new URL('../src/styles/base/_keyframes.scss', import.meta.url), 'utf8');
const settings = readFileSync(new URL('../src/views/Settings/index.tsx', import.meta.url), 'utf8');
const modelSettings = readFileSync(new URL('../src/views/ModelSettings/index.tsx', import.meta.url), 'utf8');
const button = readFileSync(new URL('../src/components/atoms/button.tsx', import.meta.url), 'utf8');
const input = readFileSync(new URL('../src/components/atoms/input.tsx', import.meta.url), 'utf8');
const select = readFileSync(new URL('../src/components/atoms/select.tsx', import.meta.url), 'utf8');
const animation = JSON.parse(readFileSync(new URL('../src/assets/animations/settings-orbit.json', import.meta.url), 'utf8'));

assert.equal(packageJson.dependencies['lottie-react'], '^3.1.0');
assert.match(tokens, /:root\[lang='en'\]/);
assert.match(tokens, /:root\[lang='ja'\]/);
assert.match(tokens, /--touch-target: 44px/);
assert.match(tokens, /line-break: strict/);
assert.match(tokens, /@media \(max-width: 380px\)/);
assert.match(atoms, /AnimatedSettingsMark/);
assert.match(atoms, /Tabs, TabsContent, TabsList, TabsTrigger/);
assert.match(tabs, /@radix-ui\/react-tabs/);
assert.match(tabs, /locale-control-label/);
assert.match(animatedSettingsMark, /prefers-reduced-motion: reduce/);
assert.match(animatedSettingsMark, /lazy\(async \(\) =>/);
assert.match(animatedSettingsMark, /data-animation="settings-orbit"/);
assert.match(molecules, /SettingsNavItem/);
assert.match(molecules, /ResourceCard/);
assert.match(atoms, /Skeleton/);
assert.match(skeleton, /skeleton-shimmer/);
assert.match(resourceCard, /variant\?: 'row' \| 'media'/);
assert.match(resourceCard, /line-clamp-1/);
assert.match(resourceCard, /line-clamp-2/);
assert.match(resourceCard, /data-resource-card/);
assert.match(resourceCard, /data-resource-actions/);
assert.match(resourceCard, /h-full/);
assert.match(molecules, /FormSection/);
assert.match(molecules, /ResourceListBoundary/);
assert.match(resourceListBoundary, /QueryErrorResetBoundary/);
assert.match(resourceListBoundary, /<Suspense fallback=\{<ResourceListSkeleton/);
assert.match(resourceListBoundary, /role="status"/);
assert.match(resourceListBoundary, /aria-busy="true"/);
assert.match(resourceListBoundary, /ResourceListPendingBar/);
assert.equal((app.match(/<ResourceListBoundary/g) || []).length, 6);
assert.match(home, /useSuspenseQueries/);
for (const view of suspenseListViews) assert.match(view, /useSuspenseQuery/);
assert.match(suspenseListViews[0], /useTransition/);
assert.match(keyframes, /@keyframes skeletonShimmer/);
assert.match(keyframes, /prefers-reduced-motion: reduce/);
assert.match(formSection, /data-form-section/);
assert.match(formSection, /border-t border-border/);
assert.doesNotMatch(formSection, /shadow-|rounded-2xl|rounded-3xl|bg-card/);
assert.match(settings, /<AnimatedSettingsMark/);
assert.match(settings, /<SettingsNavItem/);
assert.match(settings, /divide-y divide-border border-y border-border/);
assert.doesNotMatch(settings, /md:grid-cols-2|CardDescription|bg-gradient-to/);
assert.match(modelSettings, /<Tabs value=\{activeSection\}/);
assert.match(modelSettings, /data-settings-section="runtime"/);
assert.match(modelSettings, /data-settings-section="providers"/);
assert.match(modelSettings, /data-settings-section="models"/);
assert.match(modelSettings, /sticky top-\[58px\]/);
assert.match(button, /default: 'min-h-11/);
assert.match(button, /icon: 'size-11 min-h-11 min-w-11/);
assert.match(input, /min-h-11/);
assert.match(select, /min-h-11/);
assert.equal(animation.nm, 'Lorechat settings orbit');
assert.ok(animation.op > animation.ip);
assert.ok(Array.isArray(animation.layers) && animation.layers.length >= 3);

console.log('design system contract ok');
