# claude-trading-skills (vendored)

The 68 skills in `skills/` other than `trading-analysis` are copied from
[agiprolabs/claude-trading-skills](https://github.com/agiprolabs/claude-trading-skills)
at commit `981e1d736cdc02bdc1c55c74ec9224e956414706`, under the MIT license in `LICENSE.md` (Copyright (c) 2026 AGIPro).

To update, re-copy `skills/` from a newer upstream commit and change the commit above.

Some skills (`raptor-dex`, `jito-bundles`, `solana-tx-building`, `dex-execution`)
can build, sign and submit on-chain transactions. They default to demo or
simulation modes; live mode needs `PRIVATE_KEY` and asks for confirmation.
