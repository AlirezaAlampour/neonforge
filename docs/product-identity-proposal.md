# Product identity proposal — approval required before migration

NeonForge remains a reasonable name for a creative media studio. Its limitation
is discovery: the name does not immediately explain scenes, video and voice.
A clearer descriptor may provide most of the benefit without operational churn.

| Candidate product/UI name | Fit | Tradeoff |
| --- | --- | --- |
| **Forge Studio** | Short, broad home for scenes, video, voice and performance | Generic; distinguish it from the separate Forge3D application |
| **NeonForge Studio** | Keeps existing recognition and adds a clear product category | Slightly longer; recommended low-disruption option |
| **SceneForge** | Strong fit for reference-driven scenes and cinematic direction | Voiceover is less obvious from the name alone |

These are naming concepts; availability has not been established. Choose the
product name and positioning before deciding whether any technical name changes.

| Identity layer | Recommended first step | Later migration, only if approved |
| --- | --- | --- |
| Product/UI | Change visible title, metadata and documentation together | Preserve existing localStorage keys so saved scenes/preferences survive |
| GitHub repository | Keep `AlirezaAlampour/neonforge` | Separate repository rename; verify redirects, remotes, CI, links and branch protection |
| Local directory | Keep `/home/xxfactionsxx/neonforge` | Inventory absolute host-service paths, socket adapters, scripts, image overlays and the active iPhone worktree before any move |
| Docker/services | Keep `dgx-ai-stack` and `ai-*` names | No cosmetic rename needed; service DNS and lifecycle allowlists are operational contracts |
| Persistent storage | Keep all named volumes and `/srv/ai/*` paths | No rename or data move needed for a UI brand change |
| URLs | Keep existing private addresses/routes | Treat hostnames/TLS/proxy migration as its own tested change |

Reviewable migration order: approve name → UI-only brand patch and mobile check →
optional GitHub rename with normal history → separately scoped directory/URL
migration only if it has a practical benefit. Snapshot configs and retain old
links/rollback at each step. No name, directory, Docker service, volume, repository
or deployment URL has been renamed in this session.
