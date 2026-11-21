# Running a keeper

A keeper is an off-chain job that periodically calls `trigger` to keep one or more volumes funded. Calling `trigger` is permissionless: the keeper needs no authorization from the volume owner and pays only gas. The BZZ for each top-up comes from the volume's payer. This guide covers registry release `v2-rc4` on the Sepolia testnet; contract details are in [Usage](./usage.md).

Users should run their own keeper, or arrange for one they trust. Any keeper operated by the registry project is altruistic and best-effort.

## Required resources

A keeper needs:

- The chain ID and `VolumeRegistry` address: `11155111` and `0xe491308994af391a32191a8c380ca818dad332a4` for `v2-rc4` ([Usage §2](./usage.md#2-deployment)).
- The volume IDs it is responsible for, or a policy for choosing them from the active set.
- At least one RPC endpoint, preferably several from independent providers.
- A transaction-signing wallet funded with the chain's native gas token.
- A scheduler, or some other way to run periodically.
- Somewhere to send failures and warnings, such as logs or an alerting service.

The keeper wallet should be dedicated to this purpose. It needs no BZZ, no registry authorization, and no ownership of the volumes.

The volume's payer separately needs enough BZZ and allowance. A keeper cannot make up for a missing account, allowance or balance; it can only report them.

## Contract interface

The keeper surface is declared in two Solidity interfaces at tag `v2-rc4`, in [`contracts/src/interfaces/`](https://github.com/shtukaresearch/swarm-volume-registry/tree/v2-rc4/contracts/src/interfaces):

- `IVolumeRegistryKeeper`: `trigger(bytes32)`, `trigger(bytes32[])`, `reap(bytes32)`.
- `IVolumeRegistryViews`: enumeration (`getActiveVolumes`, `getActiveVolumeCount`, `getVolume`), the events, and the `SkipReason`, `RetireReason` and `Status` enums.

A keeper needs nothing else. Build its ABI from these two interfaces (`forge inspect IVolumeRegistryKeeper abi --json`, and the same for `IVolumeRegistryViews`), or take the full ABI from the deployment record ([Usage §3](./usage.md#3-abi-and-solidity-interfaces)). A keeper whose ABI holds only these functions cannot call owner or payer functions by mistake.

## Revert semantics

Keeper endpoints do not revert because of the state of a volume, an account or a payer ([ADR-0007](./adr/0007-keeper-endpoints-revert-only-on-bugs.md)). Each volume passed to `trigger` ends in exactly one of:

| Outcome | Event | Meaning |
|---|---|---|
| Topped up | `Toppedup(volumeId, amount, newNormalisedBalance)` | BZZ was added to the batch |
| Nothing due | none | The batch is at or above target |
| Retired | `VolumeRetired(volumeId, reason)` | The volume no longer exists; stop triggering it |
| Skipped | `TopupSkipped(volumeId, reason)` | The volume exists (or never did) and no BZZ moved |

All four are a successful transaction. In particular, unlike earlier releases, `trigger` on an unknown or retired ID does not revert. It emits `TopupSkipped` with reason `NotFound` (3) or `NotActive` (4).

A keeper transaction reverts only on:

- **out of gas**, the only routine cause ([Gas usage](#gas-usage));
- **a paused `PostageStamp`**, or a paused Sepolia BZZ token, which affects every volume;
- **a fault** in the registry, Postage or the token, which should be reported.

So a successful receipt means every requested volume was processed, and a revert always needs attention. A keeper does not have to tell a payment failure apart from an infrastructure failure by examining revert data: payment failures never revert.

## Calling the contract

Either entry point is fine on `v2-rc4`:

- **`trigger(bytes32)`, one transaction per volume.** A revert affects only that volume, and each volume has its own transaction hash. This is the default choice, and what the [reference keeper](#reference-implementation) does.
- **`trigger(bytes32[])`, one transaction for several volumes.** It saves the 21k transaction base cost and nonce handling for all but one volume. It is all-or-nothing: an out-of-gas or a fault on any volume rolls back the whole batch, and no volume in it is topped up. Keep batches small enough that one failure does not hold up many volumes.

The batched entry point's old problems are gone: `eth_estimateGas` now measures the real work, and a failed transfer for one volume cannot silently use up the rest of the batch's gas.

Process every configured volume even if an earlier one fails, but report the run as failed and say which volumes were affected.

## Gas usage

The gas `trigger` uses depends on the state when it executes:

| Path | Gas, Sepolia `v2-rc4` |
|---|---|
| Nothing due, skipped, or retired | ≈ 22–50k |
| Topped up | ≈ 400–850k, mostly `PostageStamp.topUp` |

An estimate taken while a batch is at target, for example in the block right after a top-up or right after another keeper's trigger, measures the cheap path. Every block drains the batch, so by the time the transaction executes there may be a deficit. A transaction sent with that estimate as its gas limit then takes the top-up path and reverts out of gas. We ran into this while testing the examples in [Usage](./usage.md): a trigger sent one block after its volume was created failed this way.

How to choose a gas limit is up to the keeper, but whatever it does has to allow for the state changing between estimation and execution. For `trigger(bytes32[])`, that applies to every ID in the batch. Only gas actually used is charged, but the wallet must hold `gasLimit × maxFeePerGas` to send the transaction.

Treat an out-of-gas revert as a keeper failure.

## Choosing volumes

`getActiveVolumes(offset, limit)` pages through the active set. To enumerate it consistently:

- Read `getActiveVolumeCount()` and every page at the same pinned block number. Retirement removes entries by swap-and-pop, so the order changes between blocks, and paging across blocks can skip or repeat volumes.
- Each `VolumeView` includes `accountActive`. A volume whose owner's account is inactive will only skip with `NoAuth`. A keeper may leave it out to save gas, or trigger it anyway so the skip is recorded.
- Anyone with an active account can register any live batch, and registering costs only gas. The active set can therefore contain volumes that duplicate a batch or will never be funded. A keeper serving particular users should trigger their volume IDs, not the whole active set.
- Several volumes can share a batch. Triggering any one of them tops up the batch, and the others then find nothing due.

## Scheduling

Run the keeper substantially more often than the runway that `graceBlocks` represents. On `v2-rc4`, `graceBlocks` is 300, about one hour; the reference keeper runs every ten minutes.

Do not treat `graceBlocks × expected block time` as a precise deadline. Block production, transaction inclusion, gas prices, RPC availability and the postage price all vary. Leave enough margin for several missed runs and delayed transactions. The worst-case survival bound is in [Usage §18](./usage.md#18-survival-guarantee).

Prevent overlapping runs that share a wallet, unless the implementation deliberately manages nonces across concurrent runs. Triggering is idempotent, so a skipped run costs nothing that the next one does not recover, as long as the runway holds.

## Deployment approaches

The keeper is stateless apart from its configuration and its list of volume IDs. It does not need a database.

Two common approaches:

- A scheduled serverless function. Store the signing key and RPC credentials in the platform's secret manager, and route each invocation's output to its logging or alerting service. Check the platform's execution-time, subrequest and cron-frequency limits.
- A cron job or systemd timer on an always-on server or VPS. Store the key with restricted file permissions, and make sure failed runs and warnings reach a log, an email address or a monitoring service.

Reliability depends more on scheduling, RPC choice, wallet funding and monitoring than on the runtime.

## RPC reliability

RPC failure is not only a refused connection. An endpoint may be stale, rate-limited, internally inconsistent, unable to estimate gas correctly, or accept a transaction without propagating it.

Use endpoints from several independent providers. Keep failover simple and observable; code cannot reliably classify every form of bad RPC behavior.

In particular:

- Check that the endpoint reports the expected chain ID.
- Wait for a receipt rather than treating submission as success.
- Keep transaction hashes, so an uncertain submission can be checked through another endpoint.
- Do not blindly resubmit an uncertain transaction with a new nonce.
- Warn whenever a fallback endpoint was needed.
- Fail the run when no configured endpoint can complete the operation.
- Use enough confirmations for the deployment's risk tolerance.

Endpoints run by the same provider may share infrastructure and fail together.

## Keeper wallet funding

Monitor the keeper wallet's balance and warn well before it runs out. A scheduled job can keep starting normally while being unable to send any transaction.

Choose the warning threshold from:

- the gas per volume, using the top-up figure ([Gas usage](#gas-usage));
- the number of volumes;
- the run frequency;
- current and unusually high base fees;
- how long you want to have to refill the wallet.

It is also useful to warn when the base fee is far above what the keeper normally sees.

If wallet funding is automated, secure the funding mechanism separately and limit what it can expose. For small deployments, refilling a dedicated keeper wallet by hand may be preferable.

## Outcomes and who acts

Record the transaction hash and the decoded outcome for each volume. Decode outcomes from the receipt's logs, filtered to the registry address, using the event ABI from `IVolumeRegistryViews`.

| Outcome | Report as | Who acts |
|---|---|---|
| `Toppedup` | ok | — |
| No event | ok: nothing was due | — |
| `TopupSkipped(NoAuth)` | warning | Owner and payer: the account is inactive |
| `TopupSkipped(PaymentFailed)` | warning | Payer: balance or allowance below the amount due |
| `TopupSkipped(NotFound)` | warning | Keeper operator: the configured ID is wrong |
| `TopupSkipped(NotActive)` | warning, then drop the ID | — the volume is Retired |
| `VolumeRetired` | warning, then drop the ID | Owner, if the volume was meant to continue |
| Reverted transaction | failure | Keeper operator: out-of-gas first, then Postage pause status, then report a fault |

Repeated skips for the same volume should escalate. A volume that keeps skipping is draining toward `BatchDied`.

## Failure and warning signals

Each run should produce one clear result, readable by machine or person, that the scheduler can route to logs or alerts.

Treat at least the following as failures:

- invalid configuration;
- an unexpected chain ID or registry address;
- no RPC endpoint reachable;
- failure to sign or send a transaction;
- insufficient gas funds;
- a reverted transaction (see [Revert semantics](#revert-semantics));
- a transaction with no receipt within the configured timeout.

Useful warnings:

- keeper wallet balance below its threshold;
- base fee far above its expected range;
- primary RPC failure or frequent failover;
- slow confirmation;
- any `TopupSkipped`;
- a retired or vanished volume;
- volumes left over because the run hit its time budget.

Do not catch these conditions and then report a successful run. If several volumes are processed in one run, report partial results, but make the run itself say that at least one operation failed.

Monitoring should answer "did the latest scheduled run maintain its volumes?", not just "is a process running?"

## Testing

Test a keeper against the Sepolia `VolumeRegistry` before relying on it. Sepolia ETH and sBZZ sources are in [Usage §20](./usage.md#20-testnet-tokens); sBZZ has no faucet.

A local fork exercises every path without spending testnet tokens:

```sh
anvil --fork-url "$SEPOLIA_RPC_URL"     # http://127.0.0.1:8545, prefunded test accounts
# Move sBZZ to a test account by impersonating a holder (fork only):
cast rpc anvil_impersonateAccount $HOLDER
cast send $BZZ "transfer(address,uint256)" $TEST_ACCOUNT $AMOUNT --from $HOLDER --unlocked
```

Then create a volume ([Usage §7](./usage.md#7-creating-a-volume)) and point the keeper at the fork. `cast rpc anvil_mine <n>` advances blocks so the batch drains. Use a separate keystore account for the fork, because the prefunded anvil keys are public.

Without any sBZZ you can still test:

- wrong chain or registry configuration;
- primary RPC failure and fallback;
- wallet balance and high-fee warnings;
- scheduler output and alert routing;
- receipt timeouts and process restarts;
- `TopupSkipped(NotFound)` handling, by triggering a made-up volume ID, which costs only gas.

A full end-to-end test needs a volume funded with sBZZ. With one, also check:

- a successful top-up sent in the block right after another top-up;
- receipt and event handling for every outcome in the table above;
- that processing continues when one configured volume fails;
- that missed runs leave the intended margin.

## Reference implementation

[`services/keeper/`](../services/keeper/README.md) is the keeper the maintainers run: a Cloudflare Worker, written in TypeScript with viem, that sends one `trigger(bytes32)` per volume, enumerates at a pinned block, decodes each receipt, and reports one structured result per run. It is one possible deployment, not a requirement.
