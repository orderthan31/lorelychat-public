import { Component, lazy, memo, Suspense, useEffect, useState, type ReactNode } from 'react';
import { useMediaMatch } from '../hooks/useMediaMatch';
import mark from '../assets/animations/lore-agent-mark.json';

const LazyLottie = lazy(async () => ({ default: (await import('lottie-react')).Lottie }));

function StillMark() {
  return <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96" width="100%" height="100%" style={{ display: 'block' }} aria-hidden="true" data-agent-mark-static>
    <path d="M30 12H61C73.7 12 84 22.3 84 35V57C84 69.7 73.7 80 61 80H38L19 91V77C11 73 7 65 7 56V35C7 22.3 17.3 12 30 12Z" fill="#5446d5" />
    <path d="M34 55V60C34 63.5 37 65 42 65H55" fill="none" stroke="#fff" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
    <ellipse cx="33" cy="41" rx="3.75" ry="5.5" fill="#fff" /><ellipse cx="56" cy="41" rx="3.75" ry="5.5" fill="#fff" />
    <path d="M79 6 82 14 90 17 82 20 79 28 76 20 68 17 76 14Z" fill="#68dbbd" stroke="#fff" strokeWidth="2.5" strokeLinejoin="round" />
  </svg>;
}

class MarkBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <StillMark /> : this.props.children; }
}

export type AgentMarkProps = { size?: number; active?: boolean; className?: string; animate?: boolean };

/** Original Folio mark: a speech-page, an L-shaped smile and a thought spark.
 * Motion is one short cycle on entry/state change, never an endless idle loop.
 * All vectors are local; reduced motion, hidden pages and errors stay static.
 */
export const AgentMark = memo(function AgentMark({ size = 28, active = false, className = '', animate = true }: AgentMarkProps) {
  const reducedMotion = useMediaMatch('(prefers-reduced-motion: reduce)');
  const [hidden, setHidden] = useState(() => typeof document !== 'undefined' && document.visibilityState === 'hidden');
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const sync = () => setHidden(document.visibilityState === 'hidden');
    document.addEventListener('visibilitychange', sync);
    return () => document.removeEventListener('visibilitychange', sync);
  }, []);
  const still = reducedMotion || hidden || failed || !animate;
  return <div className={`agent-mark ${className}`} style={{ display: 'inline-block', width: size, height: size, flexShrink: 0, lineHeight: 0, verticalAlign: 'middle' }} aria-hidden="true" data-agent-mark data-active={active || undefined}>
    {still ? <StillMark /> : <MarkBoundary><Suspense fallback={<StillMark />}>
      <LazyLottie key={active ? 'working' : 'idle'} src={mark} autoplay loop={false} speed={active ? 1.4 : 1}
        style={{ display: 'inline-block', width: size, height: size }} aria-hidden="true" data-agent-mark-animation
        rendererSettings={{ runExpressions: false }} subscriptions={{ error: () => setFailed(true) }} />
    </Suspense></MarkBoundary>}
  </div>;
});
