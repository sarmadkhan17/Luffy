# Workspace-scoped reference skills

Fetched 2026-09-27 from official maintainers, for this frontend task only.
No global agent settings or MCP servers were changed. These are the relevant
instruction/reference files, not a full CLI installation.

- UI UX Pro Max: https://github.com/nextlevelbuilder/ui-ux-pro-max-skill
  - `.claude/skills/ui-ux-pro-max/SKILL.md`
  - `references/quick-reference.md`, `references/pro-rules.md`
  - Applied the web quality checklist: contrast, keyboard/focus, labels, feedback,
    reduced motion, responsive layout and chart alternatives. No design-system
    generation or design replacement was performed, per owner instruction.
- Official Motion for React guidance: https://github.com/motiondivision/ai-kit
  - `plugins/motion/skills/motion/SKILL.md`
  - `best-practices/react.md`, `index.md`, `css-or-motion.md`
  - The official current skill is named `motion`; React guidance is its React
    reference, rather than a separate `motion-react` package.
  - Used interruptible transform animation, motion/react imports, MotionConfig,
    live reduced-motion handling and document visibility cleanup.
  - Motion MCP is not connected; self-contained guidance was sufficient:
    https://motion.dev/docs/ai-kit . No paid audit or Motion+ claim.
- Browser tooling uses the official Playwright Test CLI:
  https://playwright.dev/docs/test-cli . Existing Chromium reused; no browser
  installer or second browser automation stack was added.

Supporting official library references:
- https://reactflow.dev/learn
- https://tradingview.github.io/lightweight-charts/docs

Skills are documentation only and contribute zero browser bundle bytes.
