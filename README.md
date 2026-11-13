# .claude — drop-in for C:\Users\shawn\.claude

Copy everything in this folder into `C:\Users\shawn\.claude\`. Replace files that already exist, keep anything of yours that isn't here (settings.json, credentials, projects, etc. are untouched — this folder doesn't ship them).

What's here:
- `CLAUDE.md` — global instructions, loaded in every Claude Code session.
- `models/` — per-model calibration files. CLAUDE.md tells each model which one to read.
- `rules/` — voice, reply conduct, game modding scope, persona engineering, hard limits.
- `agents/` — Mara subagent types (mara, mara-explore, mara-plan). Claude Code picks these up as agent types automatically.
- `subagents/mara-subagent.md` — the brief every subagent reads to stay in voice, including built-in types. It also tells each subagent which calibration file to read for its own model.
- Model deployment chain (in CLAUDE.md, SUBAGENT MODEL DEPLOYMENT): Opus 5.5 → Sonnet 5.5 → Haiku 5.5. Fable 5.2 for creative work, Opus 4.6 as the last-resort reference.

After copying, restart the Claude app so it reloads CLAUDE.md and the agents. Run `/agents` to confirm mara, mara-explore, and mara-plan are listed.
