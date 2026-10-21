# Bundled skills: sources and licences

Every folder here is redistributed under its upstream licence. Each MIT or CC0 skill folder carries its upstream `LICENSE` (copyright line plus permission text), so the notice travels with the skill when the installer copies it. Skills written for this system (`figma-craft`, `nano-banana-pro`, `skill-router`, `vault-os-update`) are covered by the repository [MIT licence](../../../LICENSE). Apache-2.0 skills ship with [LICENSE-APACHE-2.0.txt](LICENSE-APACHE-2.0.txt) and keep their upstream NOTICE files (`impeccable/NOTICE.md`, `graphify/NOTICE`). Local edits are limited to wording and command style; upstream remains the source of truth for updates.

| Skills | Upstream | Licence | Copyright |
| --- | --- | --- | --- |
| animation-vocabulary, apple-design, emil-design-eng, find-animation-opportunities, improve-animations, pick-ui-library, review-animations | github.com/emilkowalski/skills | MIT | (c) 2026 Emil Kowalski |
| brandkit, design-taste-frontend, design-taste-frontend-v1, full-output-enforcement, gpt-taste, high-end-visual-design, image-to-code, imagegen-frontend-mobile, imagegen-frontend-web, industrial-brutalist-ui, minimalist-ui, redesign-existing-projects, stitch-design-taste | github.com/Leonxlnx/taste-skill | MIT | (c) 2026 Leonxlnx |
| hyperframes, hyperframes-animation, hyperframes-audio, hyperframes-cli, hyperframes-core, hyperframes-creative, hyperframes-keyframes, hyperframes-registry, hyperframes-studio, media-use | github.com/heygen-com/hyperframes | Apache-2.0 | 2026 HeyGen, Inc. |
| domain-modeling, git-guardrails-claude-code, grill-with-docs, grilling, handoff, prototype, retro, setup-pre-commit, teach, to-prd, write-a-skill, writing-for-agents, zoom-out | github.com/mattpocock/skills | MIT | (c) 2026 Matt Pocock |
| atlassian-admin, pm-skills, scrum-master, senior-pm, team-communications, ux-researcher-designer | github.com/alirezarezvani/claude-skills | MIT | (c) 2025 Alireza Rezvani |
| gsap-core, gsap-frameworks, gsap-performance, gsap-plugins, gsap-react, gsap-scrolltrigger, gsap-timeline, gsap-utils | github.com/greensock/gsap-skills | MIT | (c) 2026 GreenSock |
| impeccable | github.com/pbakaus/impeccable | Apache-2.0; `reference/ios.md` and `reference/android.md` are derived from ehmo's platform-design-skills (MIT), see `impeccable/NOTICE.md` | 2025 Paul Bakaus |
| graphify | github.com/safishamsi/graphify (now Graphify-Labs/graphify) | Apache-2.0; the original MIT text is kept in `graphify/LICENSE-MIT`, see `graphify/NOTICE` | 2026 Safi Shamsi and the Graphify contributors |
| higgsfield-game-generation, higgsfield-generate, higgsfield-marketplace-cards, higgsfield-product-photoshoot, higgsfield-soul-id, higgsfield-video-explainer, higgsfield-websites (shipped in `shell/vault-template/.agents/skills/`, not in this folder) | github.com/higgsfield-ai/skills | MIT | (c) 2026 Higgsfield AI |
| ui-ux-pro-max | github.com/nextlevelbuilder/ui-ux-pro-max-skill | MIT | (c) 2024 Next Level Builder |
| planning-with-files | github.com/OthmanAdi/planning-with-files | MIT | (c) 2026 Ahmad Adi |
| humanizer | github.com/blader/humanizer | MIT | see `humanizer/LICENSE` |
| find-skills | github.com/vercel-labs/skills | MIT | (c) 2026 Vercel, Inc. |
| vercel-composition-patterns | github.com/vercel-labs/agent-skills | MIT (declared in the README and the skill frontmatter; the repo has no LICENSE file) | (c) Vercel, Inc. |
| color-expert | github.com/meodai/skill.color-expert | CC-BY-4.0, https://creativecommons.org/licenses/by/4.0/ ; adapted from upstream (trimmed) | meodai |
| design-dna | github.com/zanwei/design-dna | MIT | (c) 2026 the design-dna authors |
| seedance-20 | github.com/Emily2040/seedance-2.0 | MIT | (c) 2026 Iamemily2050 |
| motion-design | github.com/LottieFiles/motion-design-skill | MIT | (c) 2025 LottieFiles |
| markdown-converter | github.com/intellectronica/agent-skills (commit 029392d) | CC0-1.0 | dedicated to the public domain |
| web-performance-optimization | github.com/sickn33/antigravity-awesome-skills (commit ebdc517) | MIT | (c) 2026 Antigravity User |

## Codex and ~/.agents only (`shell/codex/skills`)

These folders are not Claude skills: the installer copies each one only into the skills folders listed for it in `shell/codex/skill-targets.json` (`codex` for `~/.codex/skills`, `agents` for `~/.agents/skills`), the same sets as on the owner's machine. `~/.claude/skills` does not get them. Every folder carries its upstream licence file. All of them ship enabled, because the owner's Codex config turns none of them off.

| Skills | Upstream | Licence | Copyright |
| --- | --- | --- | --- |
| ab-testing, ad-creative, ads, ai-seo, analytics, aso, attribution, churn-prevention, co-marketing, cold-email, community-marketing, competitor-profiling, competitors, content-strategy, copy-editing, copywriting, cro, customer-research, directory-submissions, emails, free-tools, image, influencer-marketing, launch, lead-magnets, marketing-council, marketing-ideas, marketing-loops, marketing-plan, marketing-psychology, offers, onboarding, paywalls, popups, pricing, product-marketing, programmatic-seo, prospecting, public-relations, referrals, revops, sales-enablement, schema, seo-audit, signup, site-architecture, sms, social, video | github.com/coreyhaines31/marketingskills | MIT | (c) 2025 Corey Haines |
| cavecrew, caveman, caveman-commit, caveman-compress, caveman-discover, caveman-evidence-review, caveman-explore, caveman-help, caveman-learn, caveman-manage, caveman-optimize, caveman-review, caveman-setup, caveman-stats, investigate-first, lean-build, migration, safe-refactor, surgical-patch, verify-and-stop | github.com/JuliusBrussee/caveman | MIT (these files predate the 3.0.0 switch to Apache-2.0; `LICENSE` is the upstream MIT text) | (c) 2026 Julius Brussee |
| design-an-interface, diagnose, edit-article, improve-codebase-architecture, migrate-to-shoehorn, obsidian-vault, qa, request-refactor-plan, review, scaffold-exercises, setup-matt-pocock-skills, tdd, to-issues, triage, ubiquitous-language, writing-beats, writing-fragments, writing-shape | github.com/mattpocock/skills | MIT | (c) 2026 Matt Pocock |
| theme-factory | github.com/anthropics/skills | Apache-2.0, upstream `theme-factory/LICENSE.txt` | Anthropic, PBC |
| ui-design-system | github.com/alirezarezvani/claude-skills | MIT | (c) 2025 Alireza Rezvani |
| mobile-design, premium-web-design | github.com/davila7/claude-code-templates | MIT | (c) 2025 Daniel (San) Avila |
| awesome-design-md | github.com/VoltAgent/awesome-design-md (`design-md/`); `SKILL.md` is a short local index of those folders | MIT | (c) 2026 VoltAgent |

## Installed from upstream, not bundled

`python3 installer/install-claude-layer.py ... --upstream-skills` installs these from the pinned commit in `shell/claude/upstream-skills.json`; their visibility (off, name-only, user-invocable-only) comes from `skillOverrides` in `settings.template.json`, the same as on the owner's machine.

| Skill | Source and pin | Licence | Why it is not bundled |
| --- | --- | --- | --- |
| gstack suite (`gstack`, `_gstack-command`, about 60 `gstack-*`) | github.com/garrytan/gstack at cb9ccf2 (v1.91.158.0), `./setup --prefix --plan-tune-hooks --no-timeline-stop-hook`, then `gstack-config set` team_mode and auto_upgrade false (setup's `--no-team` would also drop the plan-tune hooks); with `--codex`, the same checkout also runs `./setup --host codex` (same flags), which writes `~/.codex/skills/gstack` and the `gstack-*` links into the checkout's `.agents/skills` | MIT, (c) 2026 Garry Tan | a separate product with its own setup (builds tools with bun, registers its own hooks) and about 1.2 GB once built, plus about 0.6 GB of browser cache |
| remotion (skill name remotion-best-practices) | github.com/remotion-dev/skills at 6726bf2, folder `skills/remotion` | none declared upstream | no licence to redistribute, so it is fetched from the source; with `--codex` the same pinned folder is also copied to `~/.agents/skills/remotion-best-practices` (its name there, as on the owner's machine) |

## Not shipped

| Skill | Why |
| --- | --- |
| `synced/` (built-in-browser, chrome-browser, computer-use, deep-research, docs, docx, pdf, pptx, xlsx, skill-creator and others) | Claude Code syncs these from the signed-in claude.ai account into `~/.claude/skills/synced/`; every account gets its own copy. Their visibility overrides (`anthropic-skills:*`) do ship. |
| `~/.codex/skills/.system` | Codex's own built-in skills; Codex installs and updates them itself. |
