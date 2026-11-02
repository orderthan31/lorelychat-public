import { useEffect, useState } from 'react';
import { Badge, Button, Input, Select, Slider } from '../../components/atoms';
import { useI18n } from '../../i18n/I18nProvider';
import { formatActiveMatch, formatBattleControlSummary, formatCurrentMatch } from '../../i18n/core';
import type { BattleControlsProps, BattleControlDraft } from './contracts';

const DEFAULT_BATTLE_CONTROL_DRAFT: BattleControlDraft = { action: 'none', advantage: 0.5, progress_balance: 0.5, current_phase: 'opening' };

export function BattleControls({ conversation, battleState, battleControlDraft, setBattleControlDraft, visible }: BattleControlsProps & { visible: boolean }) {
  const { locale, t } = useI18n();
  const battleParticipants = battleState?.participants || [];
  const activeMatch = battleState?.active_match;
  const battleFighters = activeMatch ? battleParticipants.filter((item) => [activeMatch.participant_a_id, activeMatch.participant_b_id].includes(item.id)) : battleParticipants;
  const battleAction = battleControlDraft?.action || 'none';
  const [battleControlOpen, setBattleControlOpen] = useState(false);
  const battleControlSummary = formatBattleControlSummary(activeMatch ? [activeMatch.participant_a_name, activeMatch.participant_b_name] : null, battleParticipants.length, locale);
  const updateBattleControl = (patch: Partial<BattleControlDraft>) => setBattleControlDraft?.((current) => ({ ...(current || DEFAULT_BATTLE_CONTROL_DRAFT), ...patch }));

  function autoSelectBattleParticipants(action: string) {
    if (action !== 'start' || activeMatch || !(battleParticipants.length === 2)) return {};
    return {
      participant_a_id: battleParticipants[0].id,
      participant_b_id: battleParticipants[1].id,
    };
  }

  function handleBattleActionChange(action: string) {
    updateBattleControl({ ...DEFAULT_BATTLE_CONTROL_DRAFT, action, ...autoSelectBattleParticipants(action) });
  }

  useEffect(() => {
    if (battleAction !== 'start' || activeMatch || battleParticipants.length !== 2) return;
    if (battleControlDraft?.participant_a_id && battleControlDraft?.participant_b_id) return;
    updateBattleControl(autoSelectBattleParticipants('start'));
  }, [battleAction, activeMatch?.id, battleParticipants.length]);

  const battleControlPanel = conversation?.genre_mode === 'battle' ? (
    <details className="battle-control-dock" open={battleControlOpen} onToggle={(e) => setBattleControlOpen(e.currentTarget.open)}>
      <summary className={`battle-control-fab ${activeMatch ? 'active' : ''}`} aria-label={t('배틀 컨트롤 열기')}>
        ⚔️ <span>{t(battleAction === 'none' ? '배틀' : battleAction === 'progress' ? '진행' : battleAction === 'end' ? '종료' : '시작')}</span>
      </summary>
      <span className="battle-control-compact-summary"><Badge>{battleControlSummary}</Badge></span>
      {battleControlOpen && (
        <div className="battle-control-popover">
          <div className="battle-control-panel" aria-label={t('배틀 컨트롤')}>
            <div className="battle-control-title-row"><strong>{t('배틀 컨트롤')}</strong><Button type="button" variant="ghost" size="icon" onClick={() => setBattleControlOpen(false)} aria-label={t('배틀 컨트롤 닫기')}>×</Button></div>
            <Select value={battleAction} onChange={(e) => handleBattleActionChange(e.target.value)}>
              <option value="none">{t('일반')}</option>
              <option value="start" disabled={!!activeMatch}>{t('배틀 시작')}</option>
              <option value="progress" disabled={!activeMatch}>{t('배틀 중')}</option>
              <option value="end" disabled={!activeMatch}>{t('배틀 종료')}</option>
            </Select>
            {activeMatch && <p className="battle-control-hint">{formatActiveMatch(activeMatch.participant_a_name, activeMatch.participant_b_name, locale)}</p>}
            {battleAction === 'end' && <p className="battle-control-hint">{t('종료 후 입력 모드는 일반으로 자동 전환됩니다.')}</p>}
            {battleAction === 'start' && (
              <div className="battle-control-fields">
                <label>{t('참가자 A')}
                  <Select value={battleControlDraft?.participant_a_id || ''} onChange={(e) => updateBattleControl({ participant_a_id: e.target.value })}>
                    <option value="">{t('선택')}</option>
                    {battleParticipants.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                  </Select>
                </label>
                <label>{t('참가자 B')}
                  <Select value={battleControlDraft?.participant_b_id || ''} onChange={(e) => updateBattleControl({ participant_b_id: e.target.value })}>
                    <option value="">{t('선택')}</option>
                    {battleParticipants.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                  </Select>
                </label>
              </div>
            )}
            {battleAction === 'progress' && (
              <div className="battle-control-fields battle-progress-fields">
                <span>{formatCurrentMatch(activeMatch?.participant_a_name || t('캐릭터 A'), activeMatch?.participant_b_name || t('캐릭터 B'), locale)}</span>
                <label className="battle-balance-control"><span>{t('우위 슬라이더')}</span>
                  <div className="battle-balance-labels"><b>{activeMatch?.participant_a_name || t('캐릭터 A')}</b><b>{t('중립')}</b><b>{activeMatch?.participant_b_name || t('캐릭터 B')}</b></div>
                  <Slider min="0" max="1" step="0.05" value={battleControlDraft?.progress_balance ?? 0.5} onChange={(e) => updateBattleControl({ progress_balance: Number(e.target.value) })} />
                </label>
              </div>
            )}
            {battleAction === 'end' && (
              <div className="battle-control-fields">
                <span>{formatCurrentMatch(activeMatch?.participant_a_name || t('캐릭터 A'), activeMatch?.participant_b_name || t('캐릭터 B'), locale)}</span>
                <label>{t('승자')}
                  <Select value={battleControlDraft?.winner_id || ''} onChange={(e) => updateBattleControl({ winner_id: e.target.value })}>
                    <option value="">{t('선택')}</option>
                    {battleFighters.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                  </Select>
                </label>
                <Input value={battleControlDraft?.decisive_moment || ''} onChange={(e) => updateBattleControl({ decisive_moment: e.target.value })} placeholder={t('결정적 장면')} />
              </div>
            )}
          </div>
        </div>
      )}
    </details>
  ) : null;
  return visible ? battleControlPanel : null;

}
