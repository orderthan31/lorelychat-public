# Lore Agent — Folio

Original project artwork: a speech-page silhouette, an L-shaped smile, two eyes and a mint thought spark. This is not an Atlassian, Rovo or Intercom asset.

- `lore-agent-mark.json`: local vector Lottie, 96×96, 120 frames at 30 fps.
- `lore-agent-mark.svg`: the matching still mark.
- `AgentMark.tsx`: the existing installed Lottie React player, a brief entry/state-change cycle, static reduced-motion/hidden-page/error fallback. No remote imagery, expressions or new player dependency.

Use the same mark for the Agent entry point, panel identity and assistant avatar. Keep a 44px accessible button target and a localized name/tooltip even when the visible trigger is icon-only.
