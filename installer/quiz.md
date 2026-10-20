# Install quiz

Ask these questions before rendering the vault.

1. What should the system call you? This becomes the exact L3 approval owner.
2. What language should Claude prefer?
3. What should the vault be called, and where should it be created? The target must be new or empty.
4. What label should the isolated client zone use? Its internal slug stays `client`.
5. The global Claude layer (10 agent types, global rules, hooks, templates, status line, 74 skills, plus gstack and Remotion from upstream) goes into `~/.claude`. If you already have your own files there, should the installer skip them (default) or back them up and replace them?
6. Should Claude run in bypass permission mode, as on the owner's machine (no permission prompts for Bash, file edits, web and agents)? Default no: `permissions.defaultMode` stays `default`. Pass `--bypass-permissions` only after an explicit yes.
7. Which MCP servers should be added for your own accounts: magnific and replicate (`--mcp default`), also higgsfield (`--mcp all`), or none? Each signs in through OAuth; no token is stored by the installer.
8. Install the optional Codex integration?
9. Install ECC into a separate code project? If yes, ask for that project path.
10. Do you explicitly approve the bundled `L0-L3 Memory Model for the Vault` and `Autonomous Reflection Delegation Policy` as owner-level L3 doctrine for this new vault?

Explain question 10 before asking: L3 contains stable owner policy. Approval binds the exact rendered doctrine pages and the audited local code by SHA-256. Autonomous reflection is off by default (evolution itself runs only on the owner's word); when turned on it is limited to completed work-zone turns, can propose only L0 to L2, cannot approve L3, and cannot access personal or client zones.

Only pass `--approve-memory-policy` after an explicit yes. If the answer is no, do not create a partial installation.
