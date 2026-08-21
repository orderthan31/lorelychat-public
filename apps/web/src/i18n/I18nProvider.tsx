import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { initialLocale, isAppLocale, LOCALE_STORAGE_KEY, translateUiText, type AppLocale, type Translator } from './core';

type I18nContextValue = {
  locale: AppLocale;
  setLocale: (locale: AppLocale) => void;
  t: Translator;
};

const I18nContext = createContext<I18nContextValue | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, updateLocale] = useState<AppLocale>(initialLocale);

  const setLocale = (nextLocale: AppLocale) => {
    if (!isAppLocale(nextLocale)) return;
    localStorage.setItem(LOCALE_STORAGE_KEY, nextLocale);
    updateLocale(nextLocale);
  };

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const value = useMemo<I18nContextValue>(() => ({
    locale,
    setLocale,
    t: (key) => translateUiText(key, locale),
  }), [locale]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const context = useContext(I18nContext);
  if (!context) throw new Error('useI18n must be used within I18nProvider');
  return context;
}
