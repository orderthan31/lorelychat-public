import { lazy, Suspense, useEffect, useState } from 'react';
import { Settings2 } from 'lucide-react';
import settingsOrbit from '../../assets/animations/settings-orbit.json';
import { cn } from '../../lib/utils';

const LazyLottie = lazy(async () => {
  const module = await import('lottie-react');
  return { default: module.Lottie };
});

export type AnimatedSettingsMarkProps = {
  className?: string;
  label: string;
};

export function AnimatedSettingsMark({ className, label }: AnimatedSettingsMarkProps) {
  const [reduceMotion, setReduceMotion] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  );

  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const sync = () => setReduceMotion(media.matches);
    sync();
    media.addEventListener('change', sync);
    return () => media.removeEventListener('change', sync);
  }, []);

  if (reduceMotion) {
    return <span className={cn('inline-grid place-items-center text-primary', className)} role="img" aria-label={label} data-motion-fallback="reduced">
      <Settings2 className="size-1/2" aria-hidden="true" />
    </span>;
  }

  return <Suspense fallback={<span className={cn('inline-grid place-items-center text-primary', className)} role="img" aria-label={label}><Settings2 className="size-1/2" aria-hidden="true" /></span>}>
    <LazyLottie
      src={settingsOrbit}
      autoplay
      loop
      className={cn('overflow-hidden', className)}
      role="img"
      aria-label={label}
      data-animation="settings-orbit"
    />
  </Suspense>;
}
