import type { Account, Address, Chain, Client, Hex, Transport } from "viem";
import { estimateContractGas, writeContract } from "viem/actions";
import { getAction } from "viem/utils";
import { registryAbi } from "../abi.js";

export type EstimateTriggerGasParameters = {
  registry: Address;
  volumeIds: readonly Hex[];
  /** Defaults to the client's account. */
  account?: Account | Address;
};

export type TriggerParameters = EstimateTriggerGasParameters & {
  chain?: Chain | null;
  /** The transaction's gas limit. Default {@link estimateTriggerGas}. */
  gas?: bigint;
  /** Fetched by viem when omitted. */
  nonce?: number;
  /** EIP-1559 fees, fetched by viem when either is omitted. */
  maxFeePerGas?: bigint;
  maxPriorityFeePerGas?: bigint;
};

export type TriggerReturnType = Hex;

/** EIP-7825: no transaction may carry a gas limit above 2^24. */
export const TX_GAS_LIMIT_CAP = 16_777_216n;

/** What a batch's gas estimate is multiplied by, for PostageStamp-dependent variance. */
export const GAS_MULTIPLIER = 2n;

/**
 * Gas reserved per volume on top of the estimate: one top-up, which has cost
 * from ~400k to ~1.5M on Sepolia.
 */
export const TOPUP_RESERVE = 1_000_000n;

/**
 * Volumes per transaction. Eight top-ups at the observed worst case of ~1.5M
 * each fit under {@link TX_GAS_LIMIT_CAP}.
 */
export const BATCH_SIZE = 8;

/**
 * The gas limit to send `trigger(volumeIds)` with: its `eth_estimateGas` times
 * {@link GAS_MULTIPLIER}, plus {@link TOPUP_RESERVE} per volume, capped at
 * {@link TX_GAS_LIMIT_CAP}.
 *
 * From v2-rc4 the estimate is meaningful (ADR-0007): nothing in the batch
 * swallows out-of-gas any more, so it measures the work actually done. That
 * work can still grow before inclusion, and running out of gas reverts the
 * whole batch. A top-up's cost depends on PostageStamp state, which the
 * multiplier covers. And a volume estimated at target takes the cheap path,
 * ~22–50k, but every block drains its batch, so by inclusion it may need a
 * top-up. No multiplier covers that, so each volume carries a top-up's worth
 * of reserve. Unused gas is not charged.
 *
 * The estimate doubles as the batch's simulation. It throws in the same cases
 * the transaction would revert: out of gas, a paused PostageStamp, or a fault.
 * viem decodes the revert against the registry's errors.
 */
export async function estimateTriggerGas<
  chain extends Chain | undefined,
  account extends Account | undefined,
>(
  client: Client<Transport, chain, account>,
  parameters: EstimateTriggerGasParameters,
): Promise<bigint> {
  const { registry, volumeIds, account = client.account } = parameters;
  // `as never`: estimateContractGas derives its parameter type through the
  // chain formatter, which viem cannot resolve against a generic client.
  // `prepare: false`, or viem first fetches nonce and fees for a local account,
  // which the estimate does not need and each costs a subrequest.
  const estimate = await getAction(
    client,
    estimateContractGas,
    "estimateContractGas",
  )({
    address: registry,
    abi: registryAbi,
    functionName: "trigger",
    args: [volumeIds],
    account,
    prepare: false,
  } as never);
  const limit = estimate * GAS_MULTIPLIER + BigInt(volumeIds.length) * TOPUP_RESERVE;
  return limit < TX_GAS_LIMIT_CAP ? limit : TX_GAS_LIMIT_CAP;
}

/**
 * Top up (or retire) several volumes in one transaction, through the batched
 * `trigger(bytes32[])`.
 *
 * Idempotent: the contract tops up to a target, so a second call in the same
 * block transfers nothing.
 *
 * Without `gas`, the limit comes from {@link estimateTriggerGas}, which throws
 * rather than sending a batch that would revert.
 *
 * @example
 * const hash = await trigger(walletClient, {
 *   registry: '0x…',
 *   volumeIds: ['0x…', '0x…'],
 * })
 */
export async function trigger<
  chain extends Chain | undefined,
  account extends Account | undefined,
>(
  client: Client<Transport, chain, account>,
  parameters: TriggerParameters,
): Promise<TriggerReturnType> {
  const { registry, volumeIds, account = client.account, chain = client.chain } = parameters;

  if (volumeIds.length === 0) throw new Error("trigger needs at least one volume");
  const gas =
    parameters.gas ?? (await estimateTriggerGas(client, { registry, volumeIds, account }));
  if (gas > TX_GAS_LIMIT_CAP) {
    throw new Error(`gas ${gas} is above the ${TX_GAS_LIMIT_CAP} transaction cap`);
  }

  // With chainId, gas, nonce and fees all supplied, viem has nothing left to
  // fill and sends without first trying `eth_fillTransaction`.
  const { nonce, maxFeePerGas, maxPriorityFeePerGas } = parameters;
  return getAction(
    client,
    writeContract,
    "writeContract",
  )({
    address: registry,
    abi: registryAbi,
    functionName: "trigger",
    args: [volumeIds],
    account,
    chain,
    gas,
    chainId: chain?.id,
    nonce,
    maxFeePerGas,
    maxPriorityFeePerGas,
  } as never);
}
