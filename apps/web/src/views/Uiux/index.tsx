import { useRef, useState } from 'react';
import { Badge, Button, Card, CardContent, CardDescription, CardHeader, CardTitle, Input, Select, Surface, Switch, Textarea } from '../../components/atoms';
import { ChatImageFrame, CommandBlockRenderer, Field, GenreModeField, Pager, TextareaWithExpand, TraitSliders, type TraitScores } from '../../components/molecules';
import { BubbleList, RuntimeSettingsPanel, type RuntimeSettingValue } from '../../components/organisms';

const sampleImage = 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 405"><defs><linearGradient id="g" x1="0" x2="1" y1="0" y2="1"><stop stop-color="%23f05a68"/><stop offset="1" stop-color="%238b5cf6"/></linearGradient></defs><rect width="720" height="405" fill="url(%23g)"/><circle cx="570" cy="98" r="58" fill="%23ffffff" fill-opacity=".35"/><rect x="64" y="246" width="592" height="74" rx="0" fill="%23ffffff" fill-opacity=".26"/><text x="64" y="124" font-family="Arial, sans-serif" font-size="46" font-weight="800" fill="white">Lorely UI/UX</text><text x="66" y="174" font-family="Arial, sans-serif" font-size="22" fill="white" fill-opacity=".9">atoms · molecules · organisms</text></svg>';

const tokenSwatches = [
  ['background', 'bg-background', 'text-foreground', '앱 기본 배경'],
  ['card', 'bg-card', 'text-card-foreground', '카드/드로어 표면'],
  ['primary', 'bg-primary', 'text-primary-foreground', '주요 CTA와 브랜드 포인트'],
  ['accent', 'bg-accent', 'text-white', '보조 강조/링크성 강조'],
  ['muted', 'bg-muted', 'text-foreground', '비활성/보조 표면'],
  ['danger', 'bg-danger', 'text-white', '삭제/실패/주의 액션'],
  ['success', 'bg-success', 'text-white', '성공/정상 상태'],
  ['warning', 'bg-warning', 'text-foreground', '주의/검토 필요'],
] as const;

const spacingScale = [
  ['1', '4px', 'h-1'],
  ['2', '8px', 'h-2'],
  ['3', '12px', 'h-3'],
  ['4', '16px', 'h-4'],
  ['6', '24px', 'h-6'],
  ['8', '32px', 'h-8'],
] as const;

const sampleCommandBlocks = [
  { type: 'note', title: '노트', text: '커맨드 결과나 시스템 보조 정보를 카드처럼 정리합니다.' },
  { type: 'checklist', title: '검증 체크', items: [{ text: '모바일 폭에서 깨지지 않음', checked: true }, { text: '색상 토큰 사용', checked: true }, { text: '콘솔 에러 없음', checked: false }] },
  { type: 'vote', title: '선택지', options: [{ label: '차분한 톤', value: 62 }, { label: '강한 액션', value: 38 }] },
];

const sampleMessages = [
  { id: 'm1', speaker_type: 'user', speaker_id: 'user', content: '디자인 시스템 샘플 한번 확인하자.' },
  {
    id: 'm2',
    speaker_type: 'character',
    speaker_id: 'sample-character',
    content: 'JSON 구조는 **유지**하고, `dialogue`는 화면에서 더 자연스럽게 보여줄게.',
    action: '샘플 캐릭터가 노트북을 돌려 화면을 펼친다.',
    thought: '구조는 단단하게, 화면은 자연스럽게.',
  },
  { id: 'm3', speaker_type: 'system', speaker_id: 'system', content: '시스템 버블은 중앙 정렬 상태 메시지에 사용합니다.' },
];

function GuideSection({ title, marker, description, children }: { title: string; marker: string; description: string; children: React.ReactNode }) {
  return <Card data-uiux-section={marker}>
    <CardHeader><CardTitle>{title}</CardTitle><CardDescription>{description}</CardDescription></CardHeader>
    <CardContent className="grid gap-4">{children}</CardContent>
  </Card>;
}

function TokenSwatch({ name, bgClass, textClass, note }: { name: string; bgClass: string; textClass: string; note: string }) {
  return <div className="grid overflow-hidden rounded-xl border border-border bg-card shadow-sm shadow-foreground/5">
    <div className={`min-h-16 ${bgClass} ${textClass} grid place-items-center px-3 text-sm font-black`}>{name}</div>
    <div className="grid gap-1 p-3 text-xs text-muted-foreground"><code className="text-card-foreground">{bgClass}</code><span>{note}</span></div>
  </div>;
}

function PatternCard({ title, description, children }: { title: string; description: string; children?: React.ReactNode }) {
  return <Card className="h-full">
    <CardHeader><CardTitle>{title}</CardTitle><CardDescription>{description}</CardDescription></CardHeader>
    {children && <CardContent>{children}</CardContent>}
  </Card>;
}

export function UiuxView() {
  const [genreMode, setGenreMode] = useState('slice_of_life');
  const [memo, setMemo] = useState('UI 가이드는 실제 앱 컴포넌트를 그대로 렌더링해서 drift를 줄인다.');
  const [enabled, setEnabled] = useState(true);
  const [traitScores, setTraitScores] = useState<TraitScores>({ confidence: 4, kindness: 4, jealousy: 2, eros: 2, aggression: 1, playfulness: 4, shyness: 2, initiative: 4 });
  const [runtimeSetting, setRuntimeSetting] = useState<RuntimeSettingValue>({ model_key: 'gemini-2.5-flash', compression_model_key: 'gemini-2.5-flash-lite', response_length_preset: 'standard' });
  const guideThreadRef = useRef<HTMLDivElement>(null);
  const guideBottomRef = useRef<HTMLDivElement>(null);

  return <section className="panel page uiux-guide grid gap-3" data-modernized="uiux design guide atomic sample marker">
    <Card>
      <CardHeader className="grid gap-2 md:grid-cols-[minmax(0,1fr)_auto] md:items-start">
        <div>
          <CardTitle>UI/UX 가이드</CardTitle>
          <CardDescription>실제 앱이 쓰는 atomic 컴포넌트를 한 화면에서 확인하는 내부 디자인 시스템 페이지입니다.</CardDescription>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Badge>tokens</Badge>
          <Badge>typography</Badge>
          <Badge>spacing</Badge>
          <Badge>atoms</Badge>
          <Badge>molecules</Badge>
          <Badge>organisms</Badge>
        </div>
      </CardHeader>
      <CardContent className="grid gap-3 text-sm text-muted-foreground">
        <p>이 페이지는 문서용 mock이 아니라 실제 `components/atoms`, `components/molecules`, `components/organisms` export와 Tailwind semantic token을 직접 렌더링합니다.</p>
        <div className="grid gap-2 rounded-xl border border-border bg-background p-3 text-card-foreground md:grid-cols-3">
          <span><strong className="block text-primary">Atoms</strong>색상·입력·버튼 primitive</span>
          <span><strong className="block text-primary">Molecules</strong>필드·페이지·렌더러 조합</span>
          <span><strong className="block text-primary">Organisms</strong>채팅·설정·패널 단위</span>
        </div>
      </CardContent>
    </Card>

    <GuideSection title="토큰 팔레트" marker="token-palette" description="CSS 변수와 Tailwind semantic color를 실제 배경/텍스트 조합으로 확인합니다.">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {tokenSwatches.map(([name, bgClass, textClass, note]) => <TokenSwatch key={name} name={name} bgClass={bgClass} textClass={textClass} note={note} />)}
      </div>
      <div className="grid gap-2 rounded-xl border border-border bg-background p-3 text-sm text-muted-foreground">
        <strong className="text-card-foreground">사용 원칙</strong>
        <span>새 컴포넌트는 `bg-primary`, `text-muted-foreground`, `border-border`처럼 의미 기반 토큰을 우선 사용합니다.</span>
      </div>
    </GuideSection>

    <GuideSection title="타이포그래피" marker="typography-scale" description="앱 전역 타이포 클래스와 본문 계층을 한 번에 비교합니다.">
      <div className="grid gap-3">
        <div className="text-display">Display · 홈/랜딩급 큰 제목</div>
        <div className="text-lg">Large · 카드 헤더와 주요 섹션 제목</div>
        <div className="text-md">Medium · 리스트 제목과 필드 그룹</div>
        <div className="text-sm text-muted-foreground">Small · 설명, 힌트, 보조 메타 텍스트</div>
        <p className="m-0 text-muted-foreground">본문은 Pretendard 기반 15px/1.55를 기준으로 읽기 편하게 유지합니다.</p>
      </div>
    </GuideSection>

    <GuideSection title="Spacing" marker="spacing-scale" description="페이지/카드/필드에서 자주 쓰는 gap과 padding 단위를 시각화합니다.">
      <div className="grid gap-3 md:grid-cols-2">
        {spacingScale.map(([step, px, heightClass]) => <div key={step} className="grid grid-cols-[52px_minmax(0,1fr)_54px] items-center gap-3 text-sm">
          <code className="text-card-foreground">{step}</code>
          <span className={`${heightClass} rounded-full bg-primary`} />
          <span className="text-muted-foreground">{px}</span>
        </div>)}
      </div>
      <div className="grid gap-2 rounded-xl border border-border bg-background p-4">
        <strong>패턴</strong>
        <span className="text-sm text-muted-foreground">섹션 내부는 `gap-4`, 밀도 높은 컨트롤 묶음은 `gap-2`~`gap-3`, 페이지 사이 간격은 `gap-3`을 기본으로 둡니다.</span>
      </div>
    </GuideSection>

    <GuideSection title="버튼 상태" marker="button-states" description="variant, size, disabled, loading-like 상태를 한 줄에서 비교합니다.">
      <div className="grid gap-3">
        <div className="flex flex-wrap gap-2">
          <Button type="button">Primary</Button>
          <Button type="button" variant="secondary">Secondary</Button>
          <Button type="button" variant="ghost">Ghost</Button>
          <Button type="button" variant="destructive">Destructive</Button>
          <Button type="button" size="sm">Small</Button>
          <Button type="button" disabled>Disabled</Button>
          <Button type="button" aria-busy="true">저장 중…</Button>
        </div>
        <div className="grid gap-2 rounded-xl border border-border bg-background p-3 text-sm text-muted-foreground">
          <span><strong className="text-card-foreground">Primary</strong>는 화면당 1개 핵심 CTA 위주.</span>
          <span><strong className="text-card-foreground">Ghost</strong>는 드로어/보조 액션처럼 조용한 chrome에 사용.</span>
          <span><strong className="text-card-foreground">Destructive</strong>는 삭제/되돌리기 어려운 액션에만 사용.</span>
        </div>
      </div>
    </GuideSection>

    <GuideSection title="카드 패턴" marker="card-patterns" description="정보 카드, 액션 카드, 경고 카드의 밀도와 계층을 비교합니다.">
      <div className="grid gap-3 md:grid-cols-3">
        <PatternCard title="정보 카드" description="설명과 메타 정보를 담는 기본 패턴입니다."><Badge>read-only</Badge></PatternCard>
        <PatternCard title="액션 카드" description="사용자가 다음 행동을 고르는 카드입니다."><Button type="button" size="sm">바로가기</Button></PatternCard>
        <Card className="h-full border-danger/40">
          <CardHeader><CardTitle>주의 카드</CardTitle><CardDescription>삭제/비가역 작업 전 확인을 유도합니다.</CardDescription></CardHeader>
          <CardContent><Button type="button" variant="destructive" size="sm">삭제 확인</Button></CardContent>
        </Card>
      </div>
    </GuideSection>

    <GuideSection title="Atoms" marker="atoms-sample" description="가장 작은 범용 UI primitive입니다.">
      <div className="grid gap-3 md:grid-cols-3">
        <label className="grid gap-1 text-sm font-semibold">Input<Input value="Lorely Chat" readOnly /></label>
        <label className="grid gap-1 text-sm font-semibold">Select<Select value="guide" onChange={() => undefined}><option value="guide">Design guide</option><option value="runtime">Runtime</option></Select></label>
        <label className="flex items-center gap-2 text-sm font-semibold">Switch<Switch checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /></label>
      </div>
      <Textarea rows={3} value={memo} onChange={(event) => setMemo(event.target.value)} />
      <div className="flex flex-wrap gap-2"><Badge>default badge</Badge><Badge>semantic token</Badge><Badge>compact label</Badge></div>
      <Surface className="grid gap-1 p-3"><strong>Surface</strong><span className="text-sm text-muted-foreground">미디어/카드 사이의 중간 표면을 잡을 때 씁니다.</span></Surface>
    </GuideSection>

    <GuideSection title="Molecules" marker="molecules-sample" description="반복되는 입력 묶음과 렌더링 블록입니다.">
      <div className="grid gap-3 md:grid-cols-2">
        <Field label="Field"><Input value="label + control pattern" readOnly /><small className="text-xs font-semibold leading-snug text-muted">폼에서 반복되는 라벨/힌트 패턴입니다.</small></Field>
        <GenreModeField value={genreMode} onChange={setGenreMode} />
      </div>
      <TextareaWithExpand label="확장 텍스트" value={memo} onChange={setMemo} rows={2} placeholder="길게 쓰는 입력 샘플" />
      <TraitSliders scores={traitScores} onChange={setTraitScores} />
      <Pager page={1} total={32} onPage={() => undefined} />
      <div className="grid gap-3 md:grid-cols-[minmax(0,320px)_minmax(0,1fr)]">
        <ChatImageFrame src={sampleImage} alt="UI/UX 샘플 이미지" />
        <div className="grid gap-2">{sampleCommandBlocks.map((block, index) => <CommandBlockRenderer key={index} block={block} index={index} />)}</div>
      </div>
    </GuideSection>

    <GuideSection title="채팅 버블 패턴" marker="chat-bubble-patterns" description="사용자/캐릭터/시스템 버블과 커맨드 블록이 실제 채팅 표면에서 어떻게 보이는지 확인합니다.">
      <div className="max-h-[420px] overflow-auto rounded-xl border border-border bg-card p-2">
        <BubbleList messages={sampleMessages as any} characters={[{ id: 'sample-character', name: '샘플 캐릭터' }] as any} participants={[]} threadRef={guideThreadRef} bottomRef={guideBottomRef} onAvatarPreview={() => undefined} renderImages={true} onGenerateTts={() => undefined} onRegenerateMessage={() => undefined} onDeleteMessage={() => undefined} onRetryMessage={() => undefined} onEnterSelectionMode={() => undefined} onToggleMessageSelection={() => undefined} selectionMode={false} selectedMessageIds={[]} />
      </div>
      <div className="grid gap-2 rounded-xl border border-border bg-background p-3 text-sm text-muted-foreground">
        <span>오른쪽 버블은 사용자, 왼쪽 버블은 캐릭터, 중앙 버블은 시스템/상태 메시지 기준입니다.</span>
        <span>커맨드 블록은 위 Molecules 섹션의 `CommandBlockRenderer`와 같은 렌더러를 사용합니다.</span>
      </div>
    </GuideSection>

    <GuideSection title="Organisms" marker="organisms-sample" description="실제 화면을 구성하는 큰 단위 컴포넌트입니다.">
      <RuntimeSettingsPanel setting={runtimeSetting} onChange={setRuntimeSetting} onSave={() => undefined} title="런타임 설정 패널" />
    </GuideSection>
  </section>;
}
