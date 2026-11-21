# Swarm Volume Registry

`VolumeRegistry` keeps Swarm postage batches alive. An owner wraps a batch in an on-chain *volume*. A separately authorized payer funds it through an ERC-20 allowance. Anyone can call `trigger` to top the batch back up to a fixed runway. The contract has no admin, no upgrade path and no custody of funds.

> [!WARNING]
> The current release, `v2-rc4`, is deployed on the **Sepolia testnet only** (chain ID
> 11155111), at `0xe491308994af391a32191a8c380ca818dad332a4`. There is no v2 mainnet
> deployment yet.

## Start here

- **[Usage](./usage.md)**: the integration reference. Deployment addresses, ABI and Solidity interfaces, setup walkthroughs with `cast`, web3.py, viem and ethers, the full API, events, errors and revert semantics, costs, and uploading with Bee.
- **[Running a keeper](./KEEPERS.md)**: operating a job that calls `trigger` on a schedule: revert semantics, gas limits, scheduling, RPC reliability and monitoring.
- **[Design](./DESIGN.md)** and the **[decision records](./adr/README.md)**: the authoritative architecture, invariants, threat model, and the reasons behind them.

## For agents

Every page here is also published as plain Markdown. Fetch it instead of the HTML:

| | |
|---|---|
| Index of pages | [`llms.txt`](https://shtukaresearch.github.io/swarm-volume-registry/llms.txt) |
| Usage and keeper guide in one file | [`llms-full.txt`](https://shtukaresearch.github.io/swarm-volume-registry/llms-full.txt) |
| One page | the page's path with `.md`, e.g. [`usage.md`](https://shtukaresearch.github.io/swarm-volume-registry/usage.md) |
| ABI | the `abi` field of the [deployment record](https://raw.githubusercontent.com/shtukaresearch/swarm-volume-registry/v2-rc4/contracts/deployments/sepolia/VolumeRegistry-v2-rc4.json) |

Each HTML page also links its Markdown with `<link rel="alternate" type="text/markdown">`. The sources are in [`docs/`](https://github.com/shtukaresearch/swarm-volume-registry/tree/main/docs) on GitHub.
