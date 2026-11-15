import { Component, lazy, Suspense, useState, type ReactNode } from 'react';
import { useMediaMatch } from '../../hooks/useMediaMatch';
import { useI18n } from '../../i18n/I18nProvider';
import chatLoading from '../../assets/animations/chat-loading.json';
import './ChatLoading.scss';

const LazyLottie = lazy(async () => ({ default: (await import('lottie-react')).Lottie }));

function StillChatMark() {
  return <svg viewBox="0 0 128 128" className="chat-loading-mark" aria-hidden="true" data-chat-loading-static>
    <ellipse cx="64" cy="103" rx="23" ry="2.5" fill="#d9d1d9" opacity=".4" />
    <path d="M44 75 41 89 60 77" fill="#ffe8ec" stroke="#f05a68" strokeWidth="3" strokeLinejoin="round" />
    <rect x="29" y="26" width="70" height="56" rx="23" fill="#ffe8ec" stroke="#f05a68" strokeWidth="3" />
    <ellipse cx="53" cy="52" rx="2" ry="3" fill="#663040" /><ellipse cx="75" cy="52" rx="2" ry="3" fill="#663040" />
    <path d="M57 62 Q64 69 71 62" fill="none" stroke="#663040" strokeWidth="2.5" strokeLinecap="round" />
    <ellipse cx="44" cy="61" rx="4.5" ry="2.5" fill="#ffb0ba" /><ellipse cx="84" cy="61" rx="4.5" ry="2.5" fill="#ffb0ba" />
    <circle cx="18" cy="47" r="4.5" fill="#f05a68" /><circle cx="109" cy="69" r="4" fill="#6c8cff" />
  </svg>;
}

// An optional visual chunk must never turn a pending room into an error page.
class AnimationBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <StillChatMark /> : this.props.children; }
}

function ChatLoadingMark() {
  const reducedMotion = useMediaMatch('(prefers-reduced-motion: reduce)');
  const [failed, setFailed] = useState(false);
  if (reducedMotion || failed) return <StillChatMark />;
  return <AnimationBoundary><Suspense fallback={<StillChatMark />}>
    <LazyLottie src={chatLoading} autoplay loop className="chat-loading-mark" aria-hidden="true" data-chat-loading-animation
      subscriptions={{ error: () => setFailed(true) }} />
  </Suspense></AnimationBoundary>;
}

export function ChatLoading() {
  const { t } = useI18n();
  return <section className="chat-loading" data-chat-loading role="status" aria-label={t('불러오는 중…')} aria-busy="true">
    <div className="chat-loading-content"><ChatLoadingMark /><span>{t('불러오는 중…')}</span></div>
  </section>;
}
