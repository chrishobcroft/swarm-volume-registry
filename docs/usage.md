# Swarm Volume Registry — Usage

An integration reference for the deployed `VolumeRegistry` contract, release `v2-rc4`. It is written for software agents that integrate with the registry on someone's behalf, and for people driving it from scripts. Examples are given for `cast` (Foundry), web3.py, viem and ethers. This page covers the contract's public behavior. Further design documentation, including the architecture, invariants and the reasoning behind them, is in the [GitHub repository](https://github.com/shtukaresearch/swarm-volume-registry).

> [!WARNING]
> **Testnet only.** `v2-rc4` is a release candidate deployed on Sepolia (chain ID
> 11155111). There is no v2 deployment on Gnosis Chain or any other mainnet yet. Sepolia
> BZZ has no value, and a release candidate can be superseded by a new deployment at a
> new address at any time. Deployed contracts are immutable: a fix ships as a new
> address, not as an upgrade. Do not build anything that holds value on this deployment.

## Contents

1. [Quickstart](#1-quickstart)
2. [Contract addresses](#2-contract-addresses)
3. [Concepts](#3-concepts)
4. [Role profiles](#4-role-profiles)
5. [Importing an existing batch](#5-importing-an-existing-batch)
6. [Safe as payer](#6-safe-as-payer)
7. [Reading state](#7-reading-state)
8. [Keeping a volume funded](#8-keeping-a-volume-funded)
9. [API reference](#9-api-reference)
10. [Events and reason codes](#10-events-and-reason-codes)
11. [Errors and revert semantics](#11-errors-and-revert-semantics)
12. [How top-ups work](#12-how-top-ups-work)
13. [Retirement](#13-retirement)
14. [Revocation](#14-revocation)
15. [Survival guarantee](#15-survival-guarantee)
16. [Cost estimation](#16-cost-estimation)
17. [Testnet tokens](#17-testnet-tokens)
18. [Uploading to Swarm with Bee](#18-uploading-to-swarm-with-bee)
19. [Not in v2](#19-not-in-v2)
20. [References](#20-references)

---

## 1. Quickstart

The quickest way to get a volume that the registry keeps funded. One key acts as owner, payer and chunk signer ([Profile A](#4-role-profiles)). For anything worth protecting, make a Safe the payer instead ([§6](#6-safe-as-payer)).

You need:

- a Sepolia RPC endpoint;
- a key holding a little Sepolia ETH for gas and some sBZZ for storage ([§17](#17-testnet-tokens)). The smallest volume, depth 17, cost under 0.001 sBZZ up front at the time of writing.

It takes four transactions:

1. `BZZ.approve(registry, amount)`: your key, as payer, lets the registry pull BZZ.
2. `designateFundingWallet(you)`: your key, as owner, names its payer.
3. `confirmAuth(you)`: your key, as payer, accepts. Your account is now active. The handshake applies even when owner and payer are the same address.
4. `createVolume(...)`: the registry mints a Postage batch and charges its first runway.

### Step 1: connect

The remaining examples assume the setup below. Environment variables:

- `RPC_URL`: a Sepolia JSON-RPC endpoint. Use your own provider for writes and for log queries; public endpoints are often rate-limited or prune log history.
- A signing key. For `cast`, a keystore account. For the others, `PRIVATE_KEY`, 0x-prefixed. Never put a funded mainnet key in an environment variable.

<!-- tabs -->
**cast**

```sh
# One-time: store the key in Foundry's encrypted keystore.
cast wallet import owner --interactive

export ETH_RPC_URL=https://...                 # cast reads this
export ETH_KEYSTORE_ACCOUNT=owner              # cast signs with this keystore account
export REGISTRY=0xe491308994af391a32191a8c380ca818dad332a4
export POSTAGE=$(cast call $REGISTRY "postage()(address)")
export BZZ=$(cast call $REGISTRY "bzz()(address)")
export YOU=$(cast wallet address)

test "$(cast chain-id)" = 11155111 || echo "not Sepolia"
```

**web3.py**

```python
# pip install "web3>=7"   (tested with web3.py 8.0)
import json
import os
import urllib.request

from eth_abi import encode
from web3 import Web3
from web3.logs import DISCARD

REGISTRY = Web3.to_checksum_address("0xe491308994af391a32191a8c380ca818dad332a4")
RECORD_URL = (
    "https://raw.githubusercontent.com/shtukaresearch/swarm-volume-registry/"
    "v2-rc4/contracts/deployments/sepolia/VolumeRegistry-v2-rc4.json"
)
ERC20_ABI = [
    {"type": "function", "name": "approve", "stateMutability": "nonpayable",
     "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "outputs": [{"type": "bool"}]},
    {"type": "function", "name": "allowance", "stateMutability": "view",
     "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "outputs": [{"type": "uint256"}]},
    {"type": "function", "name": "balanceOf", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}], "outputs": [{"type": "uint256"}]},
]
POSTAGE_ABI = [
    {"type": "function", "name": "lastPrice", "stateMutability": "view",
     "inputs": [], "outputs": [{"type": "uint64"}]},
]

w3 = Web3(Web3.HTTPProvider(os.environ["RPC_URL"]))
assert w3.eth.chain_id == 11155111, "not Sepolia"

with urllib.request.urlopen(RECORD_URL) as r:
    registry = w3.eth.contract(address=REGISTRY, abi=json.load(r)["abi"])
bzz = w3.eth.contract(address=registry.functions.bzz().call(), abi=ERC20_ABI)
postage = w3.eth.contract(address=registry.functions.postage().call(), abi=POSTAGE_ABI)

me = w3.eth.account.from_key(os.environ["PRIVATE_KEY"])


def send(call):
    """Sign and send a contract call from `me`; return the receipt or raise."""
    tx = call.build_transaction({
        "from": me.address,
        "nonce": w3.eth.get_transaction_count(me.address, "pending"),
    })
    tx_hash = w3.eth.send_raw_transaction(me.sign_transaction(tx).raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
    if receipt["status"] != 1:
        raise RuntimeError(f"reverted: 0x{tx_hash.hex()}")
    return receipt
```

**viem**

```js
// npm i viem   (tested with viem 2.58; Node ESM, e.g. a .mjs file)
import {
  createPublicClient, createWalletClient, http, erc20Abi, parseAbi,
  parseEventLogs, keccak256, encodeAbiParameters,
} from "viem";
import { sepolia } from "viem/chains";
import { privateKeyToAccount } from "viem/accounts";

const REGISTRY = "0xe491308994af391a32191a8c380ca818dad332a4";
const RECORD_URL =
  "https://raw.githubusercontent.com/shtukaresearch/swarm-volume-registry/" +
  "v2-rc4/contracts/deployments/sepolia/VolumeRegistry-v2-rc4.json";

// In TypeScript, paste the ABI into the source with `as const` instead, to get typed calls.
const { abi } = await (await fetch(RECORD_URL)).json();
const account = privateKeyToAccount(process.env.PRIVATE_KEY);
const transport = http(process.env.RPC_URL);
const client = createPublicClient({ chain: sepolia, transport });
const wallet = createWalletClient({ account, chain: sepolia, transport });

const registry = { address: REGISTRY, abi };
const bzz = {
  address: await client.readContract({ ...registry, functionName: "bzz" }),
  abi: erc20Abi,
};
const postage = {
  address: await client.readContract({ ...registry, functionName: "postage" }),
  abi: parseAbi(["function lastPrice() view returns (uint64)"]),
};

/** Simulate, send, and wait for a successful receipt. */
async function send(contract, functionName, args = []) {
  const { request } = await client.simulateContract({
    ...contract, account, functionName, args,
  });
  const hash = await wallet.writeContract(request);
  const receipt = await client.waitForTransactionReceipt({ hash });
  if (receipt.status !== "success") throw new Error(`reverted: ${hash}`);
  return receipt;
}
```

**ethers**

```js
// npm i ethers   (tested with ethers 6.17; Node ESM, e.g. a .mjs file)
import { Contract, JsonRpcProvider, Wallet, AbiCoder, keccak256 } from "ethers";

const REGISTRY = "0xe491308994af391a32191a8c380ca818dad332a4";
const RECORD_URL =
  "https://raw.githubusercontent.com/shtukaresearch/swarm-volume-registry/" +
  "v2-rc4/contracts/deployments/sepolia/VolumeRegistry-v2-rc4.json";

const { abi } = await (await fetch(RECORD_URL)).json();
const provider = new JsonRpcProvider(process.env.RPC_URL, 11155111, { staticNetwork: true });
const signer = new Wallet(process.env.PRIVATE_KEY, provider);
const registry = new Contract(REGISTRY, abi, signer);
const bzz = new Contract(await registry.bzz(), [
  "function approve(address spender, uint256 amount) returns (bool)",
  "function allowance(address owner, address spender) view returns (uint256)",
  "function balanceOf(address account) view returns (uint256)",
], signer);
const postage = new Contract(await registry.postage(), [
  "function lastPrice() view returns (uint64)",
], provider);

/** Wait for a sent transaction; `wait()` throws if it reverted. */
async function send(txPromise) {
  const tx = await txPromise;
  return tx.wait();
}
```
<!-- /tabs -->

### Step 2: authorize the account

The initial charge is `lastPrice × graceBlocks × 2^depth` PLUR, pulled in the `createVolume` transaction. The allowance must cover it and the top-ups after it. The examples approve ten times the initial charge; size it properly with [§16](#16-cost-estimation).

<!-- tabs -->
**cast**

```sh
DEPTH=17
PRICE=$(cast call $POSTAGE "lastPrice()(uint64)" | cut -d' ' -f1)
GRACE=$(cast call $REGISTRY "graceBlocks()(uint64)" | cut -d' ' -f1)
CHARGE=$(( PRICE * GRACE * (1 << DEPTH) ))    # shell arithmetic is 64-bit: fine for small depths

cast send $BZZ "approve(address,uint256)" $REGISTRY $(( CHARGE * 10 ))
cast send $REGISTRY "designateFundingWallet(address)" $YOU
cast send $REGISTRY "confirmAuth(address)" $YOU
```

**web3.py**

```python
depth = 17
charge = postage.functions.lastPrice().call() * registry.functions.graceBlocks().call() << depth

send(bzz.functions.approve(REGISTRY, charge * 10))
send(registry.functions.designateFundingWallet(me.address))
send(registry.functions.confirmAuth(me.address))
```

**viem**

```js
const depth = 17;
const price = await client.readContract({ ...postage, functionName: "lastPrice" });
const grace = await client.readContract({ ...registry, functionName: "graceBlocks" });
const charge = (price * grace) << BigInt(depth);

await send(bzz, "approve", [REGISTRY, charge * 10n]);
await send(registry, "designateFundingWallet", [account.address]);
await send(registry, "confirmAuth", [account.address]);
```

**ethers**

```js
const depth = 17n;
const charge = ((await postage.lastPrice()) * (await registry.graceBlocks())) << depth;

await send(bzz.approve(REGISTRY, charge * 10n));
await send(registry.designateFundingWallet(signer.address));
await send(registry.confirmAuth(signer.address));
```
<!-- /tabs -->

### Step 3: create the volume

<!-- tabs -->
**cast**

```sh
RECEIPT=$(cast send --json $REGISTRY "createVolume(address,uint8,uint8,uint64,bool)" $YOU $DEPTH 16 0 false)
TOPIC=$(cast sig-event "VolumeCreated(bytes32,address,bytes32,address,uint8,uint64)")
VOLUME_ID=$(jq -r --arg t "$TOPIC" '.logs[] | select(.topics[0] == $t) | .topics[1]' <<<"$RECEIPT")
BATCH_ID=$(jq -r --arg t "$TOPIC" '.logs[] | select(.topics[0] == $t) | .topics[3]' <<<"$RECEIPT")
echo "volume $VOLUME_ID  batch $BATCH_ID"
```

**web3.py**

```python
receipt = send(registry.functions.createVolume(me.address, depth, 16, 0, False))
(created,) = registry.events.VolumeCreated().process_receipt(receipt, errors=DISCARD)
volume_id = created["args"]["volumeId"]   # bytes
batch_id = created["args"]["batchId"]
print("volume 0x" + volume_id.hex(), "batch 0x" + batch_id.hex())
```

**viem**

```js
const receipt = await send(registry, "createVolume", [account.address, depth, 16, 0n, false]);
const [created] = parseEventLogs({ abi, logs: receipt.logs, eventName: "VolumeCreated" });
const { volumeId, batchId } = created.args;
```

**ethers**

```js
const receipt = await send(registry.createVolume(signer.address, depth, 16, 0, false));
const created = receipt.logs
  .map((log) => registry.interface.parseLog(log))
  .find((e) => e?.name === "VolumeCreated");
const { volumeId, batchId } = created.args;
```
<!-- /tabs -->

Parameters for `createVolume` on this deployment:

- `depth`: the batch holds `2^depth` chunks. Choose it from [§16](#16-cost-estimation). It must exceed `bucketDepth`, so the minimum is 17.
- `bucketDepth`: `16`, Postage's `minimumBucketDepth()` on Sepolia.
- `ttlExpiry`: a unix timestamp after which the volume retires, or `0` for none.
- `immutableBatch`: `false` overwrites the oldest chunks when the batch is full; `true` rejects new uploads when full.

You now have a volume. Its `volumeId` identifies it to the registry; its `batchId` is the Postage batch you upload to.

### Next

- **Upload** to the batch with Bee, using `batchId` and the chunk-signer key ([§18](#18-uploading-to-swarm-with-bee)).
- **Keep it funded.** Top-ups happen only when someone calls `trigger` ([§8](#8-keeping-a-volume-funded)).
- **Watch the allowance.** When it runs out, top-ups stop ([§16](#16-cost-estimation)).

## 2. Contract addresses

### Sepolia testnet (chain ID 11155111), release `v2-rc4`

| | |
|---|---|
| `VolumeRegistry` | `0xe491308994af391a32191a8c380ca818dad332a4` |
| `PostageStamp` | `0xcdfdC3752caaA826fE62531E0000C40546eC56A6` |
| BZZ (Sepolia test token, "sBZZ") | `0x543dDb01Ba47acB11de34891cD86B675F04840db` |
| `PriceOracle` | `0x95Dc18380e92C13E4F8a4e94C99FB1b97250174B` |
| `graceBlocks` | `300` (≈ 1 h at 12-second blocks) |
| First block, for log queries | `12052651` |

The short `graceBlocks` lets top-up and expiry cycles be observed on a testnet timescale.

**Superseded releases.** `v2-rc1`, `v2-rc2` and `v2-rc3` are earlier Sepolia deployments and are no longer maintained. An earlier v1 registry exists on Gnosis Chain. It has different semantics, including unilateral ownership transfer, and is not covered by this documentation. Do not use any of them for new work. [`contracts/deployments/sepolia/VolumeRegistry.json`](../contracts/deployments/sepolia/VolumeRegistry.json) always names the current Sepolia release.

### ABI and Solidity interfaces

The `abi` field of the release's deployment record is the complete ABI. Pin it to the release tag:

```sh
curl -s https://raw.githubusercontent.com/shtukaresearch/swarm-volume-registry/v2-rc4/contracts/deployments/sepolia/VolumeRegistry-v2-rc4.json \
  | jq .abi > VolumeRegistry.abi.json
```

The public surface is also declared per actor as Solidity interfaces in [`contracts/src/interfaces/`](https://github.com/shtukaresearch/swarm-volume-registry/tree/v2-rc4/contracts/src/interfaces):

| Interface | Contents |
|---|---|
| `IVolumeRegistryViews` | Every type and enum, event, error, wiring getter and view. The read side shared by all actors. |
| `IVolumeRegistryOwner` | `createVolume`, `registerVolume`, `importVolume`, `deleteVolume`, `designateFundingWallet`, `revoke` |
| `IVolumeRegistryPayer` | `confirmAuth`, `revoke` |
| `IVolumeRegistryKeeper` | `trigger(bytes32)`, `trigger(bytes32[])`, `reap` |
| `IVolumeRegistry` | All of the above |

The actor interfaces contain write functions only; pair one with `IVolumeRegistryViews`. Each function's NatSpec states its preconditions, errors and events. To use them from a contract in a Foundry project:

```sh
forge install shtukaresearch/swarm-volume-registry@v2-rc4
echo 'swarm-volume-registry/=lib/swarm-volume-registry/contracts/src/' >> remappings.txt
```

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import {IVolumeRegistryViews} from "swarm-volume-registry/interfaces/IVolumeRegistryViews.sol";

contract VolumeCheck {
    address constant REGISTRY = 0xE491308994aF391a32191A8c380ca818dad332A4;

    function isActive(bytes32 volumeId) external view returns (bool) {
        return IVolumeRegistryViews(REGISTRY).getVolume(volumeId).status
            == IVolumeRegistryViews.Status.Active;
    }
}
```

## 3. Concepts

`VolumeRegistry` keeps Swarm postage batches alive. It wraps each batch in an on-chain *volume* that has an owner, a separately authorized payer, and a permissionless keeper API. The contract has no owner or admin, is not upgradeable, and has no mutable parameters.

- **Volume.** A registry record bound to one Postage batch for its whole life. It is identified by `volumeId`.
- **`volumeId` is not the batch ID.** `volumeId = keccak256(abi.encode(owner, nonce))`, where `nonce` is the owner's account nonce before the creating call. The batch is `batchId`, which Postage assigns. One batch can underlie several volumes, of different owners or of the same owner. Use `volumeId` with the registry and `batchId` with Postage and Bee.
- **Owner.** The address that made the creating call. The owner is permanent: v2 has no ownership transfer. The owner creates, imports and deletes volumes, and designates a payer.
- **Chunk signer.** The Postage batch owner: the key that signs uploaded chunks. For `createVolume` the owner chooses it. For an imported batch it is read from Postage. The registry never needs its signature.
- **Payer ("funding wallet").** Holds BZZ and has approved the registry. Authorized by a two-step handshake and revocable by either side.
- **Account.** `accounts[owner] = (payer, active, nonce)`. One per owner. The payer is looked up at trigger time, so a single `revoke` affects every volume of that owner.
- **Keeper.** Anyone who calls `trigger`. A keeper pays gas only and needs no authorization.
- **Units.** BZZ amounts are in PLUR (1 BZZ = 10¹⁶ PLUR). Postage prices are in PLUR per chunk per block.

## 4. Role profiles

### Profile A: single key

The owner, payer and chunk signer are the same EOA.

- **Use for:** experiments, development, demos.
- **If the key is compromised:** everything is exposed. The attacker can create volumes up to the outstanding allowance, delete volumes, and sign chunks.

### Profile B: Safe as payer

The owner is an EOA, which also signs chunks. The payer is a Safe or another smart account that can call `approve` and `confirmAuth`.

- **Use for:** any volume worth protecting.
- **If the owner key is compromised:** the attacker can spend payer funds up to the Safe's current allowance to the registry. Keep that allowance near projected needs rather than `type(uint256).max`. A single `revoke(owner)` from the Safe stops all top-ups for the owner. Because the owner here is also the chunk signer, the attacker can also call `PostageStamp.increaseDepth` directly, which makes the next trigger retire the volume (`DepthChanged`).
- **If the Safe is compromised:** that is outside what the registry protects.

An owner separate from the chunk signer is supported but is advanced usage.

## 5. Importing an existing batch

Two entry points bring an existing, live Postage batch under management. Both require the caller to have an active account. Neither requires any relation between the caller and the batch owner. `chunkSigner` and `depth` are read from Postage.

| | `registerVolume(batchId, ttlExpiry)` | `importVolume(batchId, ttlExpiry)` |
|---|---|---|
| Moves BZZ | No | Yes: tops the batch up to the `graceBlocks` target in the same transaction |
| Fails if the top-up cannot happen | No. The volume is recorded; later triggers may skip. | Yes: reverts `ImportTopupFailed` and nothing persists |
| Survival guarantee ([§15](#15-survival-guarantee)) | Only after the first successful top-up | From the transaction onward |
| Use when | You only want the registry to track the batch for now | You want the batch funded now, or nothing |

Both revert `BatchNotLive` if the batch does not exist or is exhausted, and `BatchDepthTooSmall` if its depth does not exceed Postage's `minimumBucketDepth` (16).

<!-- tabs -->
**cast**

```sh
cast send --json $REGISTRY "importVolume(bytes32,uint64)" $BATCH_ID 0 \
  | jq -r --arg t "$(cast sig-event 'VolumeImported(bytes32,address,bytes32,address,uint8,uint64,uint256)')" \
       '.logs[] | select(.topics[0] == $t) | .topics[1]'
```

**web3.py**

```python
receipt = send(registry.functions.importVolume(batch_id, 0))
(imported,) = registry.events.VolumeImported().process_receipt(receipt, errors=DISCARD)
volume_id = imported["args"]["volumeId"]
```

**viem**

```js
const receipt = await send(registry, "importVolume", [batchId, 0n]);
const [imported] = parseEventLogs({ abi, logs: receipt.logs, eventName: "VolumeImported" });
```

**ethers**

```js
const receipt = await send(registry.importVolume(batchId, 0));
const imported = receipt.logs.map((l) => registry.interface.parseLog(l)).find((e) => e?.name === "VolumeImported");
```
<!-- /tabs -->

Like `trigger`, `importVolume` costs much more when it tops up than when the batch is already at target, so its gas estimate depends on the state at the moment of estimation ([§8](#8-keeping-a-volume-funded)).

Anyone with an active account can register any live batch, so a batch you own may also underlie other people's volumes. Top-ups are idempotent per batch: whichever volume is triggered first pays, and the others find nothing to pay. Do not assume a batch maps to one volume.

## 6. Safe as payer

This is Profile B. The owner EOA calls `designateFundingWallet(SAFE)`. The Safe then executes two calls, ideally in one Safe transaction through `MultiSendCallOnly`:

1. `BZZ.approve(REGISTRY, AMOUNT)`
2. `VolumeRegistry.confirmAuth(OWNER)`

On success the registry emits `AccountActivated(OWNER, SAFE)`. The owner then calls `createVolume` as in [§1](#1-quickstart).

With the Safe Protocol Kit (tested with `@safe-global/protocol-kit` 8.0), signing with a Safe owner key:

```js
import Safe from "@safe-global/protocol-kit";
import { encodeFunctionData, erc20Abi, parseAbi } from "viem";

const kit = await Safe.init({ provider: RPC_URL, signer: SAFE_OWNER_PRIVATE_KEY, safeAddress: SAFE });
const tx = await kit.createTransaction({
  transactions: [   // more than one → batched through MultiSendCallOnly
    { to: BZZ, value: "0",
      data: encodeFunctionData({ abi: erc20Abi, functionName: "approve", args: [REGISTRY, AMOUNT] }) },
    { to: REGISTRY, value: "0",
      data: encodeFunctionData({ abi: parseAbi(["function confirmAuth(address owner)"]),
                                 functionName: "confirmAuth", args: [OWNER] }) },
  ],
});
const signed = await kit.signTransaction(tx);
await kit.executeTransaction(signed);   // a 1-of-1 Safe; otherwise collect signatures first
```

For a Safe with a threshold above one, propose the same transaction to the Safe Transaction Service with `@safe-global/api-kit`, or build it in the Safe web app's Transaction Builder, and collect signatures there. See the [Safe documentation](https://docs.safe.global).

Size `AMOUNT` to cover a bounded period ([§16](#16-cost-estimation)) rather than `type(uint256).max`. The allowance is the most a compromised owner key can spend.

## 7. Reading state

| View | Returns |
|---|---|
| `getVolume(volumeId)` | `VolumeView`. An unknown ID returns zeroes with `status = 0` (`None`). |
| `getActiveVolumes(offset, limit)` | `VolumeView[]`, a page of the active set. Order is not stable across retirements. |
| `getActiveVolumeCount()` | Size of the active set |
| `getAccount(owner)` | `Account(payer, active, nonce)` |
| `designated(owner)` | The designated payer before confirmation, or `0x0` |

`VolumeView` is `(volumeId, batchId, owner, payer, chunkSigner, createdAt, ttlExpiry, depth, status, accountActive)`. `payer` and `accountActive` are resolved from the owner's account at read time.

<!-- tabs -->
**cast**

```sh
VIEW="(bytes32,bytes32,address,address,address,uint64,uint64,uint8,uint8,bool)"
cast call $REGISTRY "getVolume(bytes32)($VIEW)" $VOLUME_ID
cast call $REGISTRY "getActiveVolumeCount()(uint256)"
cast call $REGISTRY "getActiveVolumes(uint256,uint256)($VIEW[])" 0 100
cast call $REGISTRY "getAccount(address)((address,bool,uint64))" $YOU

# The batch itself, from Postage. Remaining per-chunk balance = normalisedBalance − currentTotalOutPayment.
cast call $POSTAGE "batches(bytes32)(address,uint8,uint8,bool,uint256,uint256)" $BATCH_ID
cast call $POSTAGE "currentTotalOutPayment()(uint256)"
```

**web3.py**

```python
v = registry.functions.getVolume(volume_id).call()   # tuple, in VolumeView order
volume_id, batch_id, owner, payer, signer, created_at, ttl, depth, status, active = v

count = registry.functions.getActiveVolumeCount().call()
page = registry.functions.getActiveVolumes(0, 100).call()
payer, active, nonce = registry.functions.getAccount(me.address).call()
```

**viem**

```js
const v = await client.readContract({ ...registry, functionName: "getVolume", args: [volumeId] });
// { volumeId, batchId, owner, payer, chunkSigner, createdAt, ttlExpiry, depth, status, accountActive }
const page = await client.readContract({ ...registry, functionName: "getActiveVolumes", args: [0n, 100n] });
```

**ethers**

```js
const v = await registry.getVolume(volumeId);   // Result; v.status, v.batchId, … or v.toObject()
const page = await registry.getActiveVolumes(0, 100);
```
<!-- /tabs -->

Past events need an RPC endpoint that serves `eth_getLogs` back to block `12052651`. Both creating events index `volumeId`, `owner` and `batchId`, so you can filter on any of them.

### Predicting the next volume ID

A creating call returns `volumeId`, but a return value is not available from a receipt, which is why the examples read it from the event. It can also be computed before sending: it is `keccak256(abi.encode(owner, nonce))`, with `nonce` from `getAccount(owner)`, provided the owner's transactions land in order.

<!-- tabs -->
**cast**

```sh
NONCE=$(cast call $REGISTRY "getAccount(address)((address,bool,uint64))" $YOU | tr -d '()' | cut -d, -f3 | tr -d ' ')
cast keccak $(cast abi-encode "f(address,uint64)" $YOU $NONCE)
```

**web3.py**

```python
nonce = registry.functions.getAccount(me.address).call()[2]
next_volume_id = w3.keccak(encode(["address", "uint64"], [me.address, nonce]))
```

**viem**

```js
const { nonce } = await client.readContract({ ...registry, functionName: "getAccount", args: [account.address] });
const nextVolumeId = keccak256(encodeAbiParameters([{ type: "address" }, { type: "uint64" }], [account.address, nonce]));
```

**ethers**

```js
const { nonce } = await registry.getAccount(signer.address);
const nextVolumeId = keccak256(AbiCoder.defaultAbiCoder().encode(["address", "uint64"], [signer.address, nonce]));
```
<!-- /tabs -->

## 8. Keeping a volume funded

Top-ups happen only when someone calls `trigger(volumeId)`. Anyone can call it for any volume and pays only gas; the BZZ comes from the volume's payer. Calling it when nothing is due changes nothing.

The maintainers run an altruistic keeper against the current Sepolia release on a best-effort basis. Do not rely on it for anything you care about: run a keeper yourself or arrange for one you trust. [Running a keeper](./KEEPERS.md) covers the keeper interface, scheduling, gas and monitoring.

To top up a volume by hand:

<!-- tabs -->
**cast**

```sh
cast send $REGISTRY "trigger(bytes32)" $VOLUME_ID
```

**web3.py**

```python
receipt = send(registry.functions["trigger(bytes32)"](volume_id))
```

**viem**

```js
const receipt = await send(registry, "trigger", [volumeId]);
```

**ethers**

```js
const receipt = await send(registry["trigger(bytes32)"](volumeId));   // overloaded: name the signature
```
<!-- /tabs -->

The transaction succeeds whatever state the volume is in. Its events say what happened: `Toppedup`, `TopupSkipped` with a reason, `VolumeRetired`, or nothing if no top-up was due ([§10](#10-events-and-reason-codes)). Its gas cost depends on whether a top-up is due when it executes, so a gas estimate taken just after a top-up can be far too low ([Running a keeper](./KEEPERS.md#gas-usage)).

## 9. API reference

All functions are on the single `VolumeRegistry` contract. Signatures are exact.

### 9.1 Owner

```solidity
function createVolume(address chunkSigner, uint8 depth, uint8 bucketDepth, uint64 ttlExpiry, bool immutableBatch)
    external returns (bytes32 volumeId);
```
Mints a Postage batch owned by `chunkSigner` and records a volume owned by `msg.sender`. Charges `lastPrice × graceBlocks × 2^depth` PLUR to the caller's payer. Requires an active account. Emits `VolumeCreated`.

```solidity
function registerVolume(bytes32 batchId, uint64 ttlExpiry) external returns (bytes32 volumeId);
function importVolume(bytes32 batchId, uint64 ttlExpiry) external returns (bytes32 volumeId);
```
Record an existing live batch as a volume owned by `msg.sender`. `importVolume` also tops it up atomically. See [§5](#5-importing-an-existing-batch). Both emit `VolumeImported`, and `importVolume` may also emit `Toppedup`.

```solidity
function deleteVolume(bytes32 volumeId) external;
```
Owner only. Retires the volume (`OwnerDeleted`) and removes it from the active set. This is registry bookkeeping only: the batch is untouched and Postage has no refund path. It keeps its remaining balance until it expires, and other volumes on the same batch are unaffected.

```solidity
function designateFundingWallet(address payer) external;   // address(0) clears
```
Sets `designated[msg.sender] = payer`. It grants nothing until the payer calls `confirmAuth(msg.sender)`. Emits `PayerDesignated`. Never reverts.

```solidity
function revoke(address owner) external;   // also callable by the payer
```
See [§14](#14-revocation).

### 9.2 Payer

```solidity
function confirmAuth(address owner) external;
```
Requires `designated(owner) == msg.sender`. Sets `accounts[owner] = (msg.sender, active = true)`, replacing any previous payer, and preserves the nonce. Emits `AccountActivated`.

```solidity
function revoke(address owner) external;
```
Callable by `owner` or by its current payer. Sets `active = false`. It does not clear the designation, the payer or the nonce, and does not retire volumes. Emits `AccountRevoked`.

### 9.3 Keeper

```solidity
function trigger(bytes32 volumeId) external;
function trigger(bytes32[] calldata volumeIds) external;
function reap(bytes32 volumeId) external;
```
Permissionless. See [§8](#8-keeping-a-volume-funded) and [Running a keeper](./KEEPERS.md).

### 9.4 Views and wiring

```solidity
function getVolume(bytes32 volumeId) external view returns (VolumeView memory);
function getActiveVolumes(uint256 offset, uint256 limit) external view returns (VolumeView[] memory);
function getActiveVolumeCount() external view returns (uint256);
function getAccount(address owner) external view returns (Account memory);
function designated(address owner) external view returns (address);
function nextNonce() external view returns (uint256);   // salts minted batch IDs; unrelated to volume IDs

function postage() external view returns (address);
function bzz() external view returns (address);
function graceBlocks() external view returns (uint64);
function bzzPermitExpiry() external view returns (bool);
```

```solidity
struct VolumeView {
    bytes32 volumeId;
    bytes32 batchId;
    address owner;
    address payer;          // accounts[owner].payer, read time
    address chunkSigner;
    uint64  createdAt;      // timestamp of the creating call (start of tracking, not batch age)
    uint64  ttlExpiry;      // 0 = no expiry
    uint8   depth;
    Status  status;         // uint8 on the wire: 0 None, 1 Active, 2 Retired
    bool    accountActive;  // accounts[owner].active, read time
}

struct Account {
    address payer;
    bool    active;
    uint64  nonce;          // next creating call yields keccak256(abi.encode(owner, nonce))
}
```

## 10. Events and reason codes

| Event | topic0 | Emitted when |
|---|---|---|
| `VolumeCreated(bytes32 indexed volumeId, address indexed owner, bytes32 indexed batchId, address chunkSigner, uint8 depth, uint64 ttlExpiry)` | `0xffb4423d…6ea233` | `createVolume` succeeded |
| `VolumeImported(bytes32 indexed volumeId, address indexed owner, bytes32 indexed batchId, address chunkSigner, uint8 depth, uint64 ttlExpiry, uint256 normalisedBalance)` | `0x12d8e0e1…5e951f` | `registerVolume` or `importVolume` succeeded. `normalisedBalance` is read before any top-up. |
| `VolumeRetired(bytes32 indexed volumeId, uint8 reason)` | `0xb00942a6…8127f4` | The volume became Retired ([§13](#13-retirement)) |
| `PayerDesignated(address indexed owner, address payer)` | `0x16b02f7a…987821` | `designateFundingWallet` |
| `AccountActivated(address indexed owner, address indexed payer)` | `0xa812e6ae…3da1c7` | `confirmAuth` |
| `AccountRevoked(address indexed owner, address indexed payer, address revoker)` | `0xe60b9497…3645ba` | `revoke` |
| `Toppedup(bytes32 indexed volumeId, uint256 amount, uint256 newNormalisedBalance)` | `0xbbdb04f4…c0b674` | A trigger pulled `amount` PLUR from the payer into the batch |
| `TopupSkipped(bytes32 indexed volumeId, uint8 reason)` | `0x15ac22a1…e672ba` | A trigger moved no BZZ for the reason given |

Compute a full topic0 with `cast sig-event "<signature>"`, or read it from the ABI with your client library.

**`TopupSkipped.reason`**

| Value | Name | Meaning | Who acts |
|---|---|---|---|
| 1 | `NoAuth` | The owner's account is not active (revoked, or never activated) | Owner and payer |
| 2 | `PaymentFailed` | The payer's BZZ balance or allowance is below the amount due | Payer |
| 3 | `NotFound` | No volume has this ID | Whoever supplied the ID; it is wrong |
| 4 | `NotActive` | The volume is Retired | Nobody: drop it from the list |

**`VolumeRetired.reason`**

| Value | Name |
|---|---|
| 1 | `OwnerDeleted` |
| 2 | `VolumeExpired` |
| 3 | `BatchDied` |
| 4 | `DepthChanged` |
| 5 | `BatchOwnerMismatch` |

**`Status`**: 0 `None` (unknown ID), 1 `Active`, 2 `Retired`.

Value 0 is never emitted for either reason enum.

## 11. Errors and revert semantics

`trigger` and `reap` never revert because of the state of a volume, an account or a payer: the outcome for each volume is reported by an event ([§10](#10-events-and-reason-codes)). They revert only on out-of-gas, a paused `PostageStamp` or BZZ token, or a fault (details in [Running a keeper](./KEEPERS.md#revert-semantics)).

### Owner and payer endpoints revert with named errors

| Error | Selector | Raised by | Meaning |
|---|---|---|---|
| `AccountNotActive()` | `0x4d571464` | `createVolume`, `registerVolume`, `importVolume` | Caller has no active account: run the [§1](#1-quickstart) handshake |
| `ZeroAddress()` | `0xd92e233d` | `createVolume` | `chunkSigner` is `0x0` |
| `BatchNotLive()` | `0xe004958b` | `registerVolume`, `importVolume` | Batch missing or exhausted |
| `BatchDepthTooSmall()` | `0xe875cf5f` | `registerVolume`, `importVolume` | Batch depth ≤ Postage `minimumBucketDepth` |
| `ImportTopupFailed()` | `0xf60090aa` | `importVolume` | The top-up could not happen: payer short of balance or allowance, or `ttlExpiry` already past |
| `NotVolumeOwner()` | `0x431be6b7` | `deleteVolume` | Caller is not the owner, or the ID is unknown |
| `VolumeNotActive()` | `0x2f607c28` | `deleteVolume` | Already Retired |
| `NotDesignated()` | `0x69f3dfcd` | `confirmAuth` | Caller is not `designated(owner)` |
| `NotAuthorizedToRevoke()` | `0xb4a8529c` | `revoke` | Caller is neither the owner nor its payer |

`createVolume` does not precheck the payer. If the payer's balance or allowance is short, it reverts with the token's error, such as `ERC20: insufficient allowance` on Sepolia BZZ. Postage's own errors, such as a paused contract, propagate unchanged.

Decoding: viem names the error as `error.cause.data.errorName`; ethers as `error.revert.name`; web3.py raises `ContractCustomError` with the selector in `.data`; `cast` prints `custom error 0x…`. Look the selector up in the table above.

## 12. How top-ups work

On each `trigger(volumeId)` the registry:

1. Loads the volume. Unknown → `TopupSkipped(NotFound)`; Retired → `TopupSkipped(NotActive)`. Stop.
2. Reads the batch from Postage. Missing or expired → retire `BatchDied`. Stop.
3. Batch owner ≠ recorded chunk signer → retire `BatchOwnerMismatch`. Stop.
4. Batch depth ≠ recorded depth → retire `DepthChanged`. Stop.
5. `ttlExpiry` set and passed → retire `VolumeExpired`. Stop.
6. Owner's account inactive → `TopupSkipped(NoAuth)`. Stop. The volume stays Active.
7. `target = lastPrice × graceBlocks`; `remaining = normalisedBalance − currentTotalOutPayment`; `deficit = max(0, target − remaining)`. If `deficit == 0`, stop silently.
8. `amount = deficit × 2^depth`. If the payer's balance or allowance is below `amount` → `TopupSkipped(PaymentFailed)`. Stop. The volume stays Active.
9. Pulls `amount` from the payer, calls `PostageStamp.topUp(batchId, deficit)`, emits `Toppedup`.

Retirement checks come before the auth and payment checks, so a lapsed account never hides a dead batch.

Triggers are idempotent within a block at a constant price. A second trigger in the same block finds no deficit, and so does a trigger of another volume on the same batch. Across blocks, each trigger pays only what has drained since the last one. Nothing is lost by triggering too often except gas.

After a `NoAuth` or `PaymentFailed` skip, top-ups resume on the next trigger once the account is re-activated or the payer is funded. Until then the batch drains towards death.

## 13. Retirement

Retirement is terminal. A Retired volume cannot be revived, is never topped up again, and leaves the active set. Its ID is never reused.

| Reason | Cause | Detected by |
|---|---|---|
| `OwnerDeleted` | Owner called `deleteVolume` | Immediate |
| `VolumeExpired` | `ttlExpiry != 0` and `block.timestamp ≥ ttlExpiry` | Next `trigger` or `reap` |
| `BatchDied` | Postage reports the batch missing or its balance exhausted | Next `trigger` |
| `DepthChanged` | The chunk signer called `PostageStamp.increaseDepth` directly | Next `trigger` |
| `BatchOwnerMismatch` | Postage reports a different batch owner. Defensive; current Postage cannot do this. | Next `trigger` |

To continue after a retirement, create or import a new volume. After `DepthChanged`, the same batch, now deeper, can be registered again under a new volume ID.

## 14. Revocation

`revoke(owner)` is the emergency stop for an owner's account.

- Callable by the owner or by the confirmed payer, in one transaction.
- Sets `accounts[owner].active = false`. Every volume of that owner then skips with `NoAuth`, because the payer is resolved at trigger time.
- Does **not** retire volumes. They keep their remaining batch balance until it runs out (`BatchDied`). Call `deleteVolume` on each if you want them retired now. That stops tracking but does not destroy the batch.
- Does **not** clear `designated[owner]`. The designated payer can re-activate with `confirmAuth(owner)` without the owner doing anything.

For a permanent separation: the owner calls `designateFundingWallet(address(0))` and either side calls `revoke(owner)`. As a separate defense, the payer sets its BZZ allowance to the registry to zero. Clearing the designation alone does not deactivate an active account.

To switch to a new payer, the owner designates it and the new payer calls `confirmAuth`. This replaces the old payer atomically. The nonce, and so the sequence of future volume IDs, is preserved.

v2 has no ownership transfer, so no one can attach a volume to your account or make your payer fund someone else's volume. The only volumes that charge your payer are those created by the owner you confirmed.

## 15. Survival guarantee

If a volume was last created, imported or topped up at block `t0`, its batch survives at least `f × graceBlocks` blocks after `t0` even if nobody triggers it again. The bound holds under the fastest price rise Swarm's `PriceOracle` permits.

For this deployment (`graceBlocks = 300`): `f ≈ 0.9992`, so at least ≈ 299.8 blocks, about 60 minutes at 12-second blocks. Under flat or falling prices the runway is at least the full `graceBlocks`. A volume created with `registerVolume` has no guarantee until its first successful top-up.

The bound assumes the PriceOracle's rate-limited path. An account with `PRICE_ORACLE_ROLE` can set the price directly. A paused Postage blocks top-ups while batches keep draining. If Postage's administrator raises `minimumValidityBlocks` above this deployment's `graceBlocks`, top-ups revert until users move to a compatible deployment.

Wall-clock time is not guaranteed: Sepolia block times vary. Trigger well inside the runway ([Running a keeper](./KEEPERS.md#scheduling)).

## 16. Cost estimation

### BZZ for storage

**Pick a depth.** The effective capacity of a batch at each depth (unencrypted, no erasure coding) is:

| `depth` | Effective volume | | `depth` | Effective volume |
|---|---|---|---|---|
| 17 | 44.70 kB | | 30 | 4.17 TB |
| 18 | 6.66 MB | | 31 | 8.45 TB |
| 19 | 112.06 MB | | 32 | 17.07 TB |
| 20 | 687.62 MB | | 33 | 34.36 TB |
| 21 | 2.60 GB | | 34 | 69.04 TB |
| 22 | 7.73 GB | | 35 | 138.54 TB |
| 23 | 19.94 GB | | 36 | 277.72 TB |
| 24 | 47.06 GB | | 37 | 556.35 TB |
| 25 | 105.51 GB | | 38 | 1.11 PB |
| 26 | 227.98 GB | | 39 | 2.23 PB |
| 27 | 476.68 GB | | 40 | 4.46 PB |
| 28 | 993.65 GB | | 41 | 8.93 PB |
| 29 | 2.04 TB | | | |

Pick the smallest depth whose effective volume exceeds your payload. To store 1 GB you need `depth = 21`. Effective volume is far below `2^depth × 4 KiB` because chunks spread unevenly across buckets and a batch fills by bucket, not by chunk count. For encrypted or erasure-coded uploads, see the [upstream table](https://docs.ethswarm.org/docs/concepts/incentives/postage-stamps). Cost scales with `2^depth`, not with what you store.

**Price it.** Read `lastPrice` from Postage, in PLUR per chunk per block:

```sh
cast call $POSTAGE "lastPrice()(uint64)"
cast call $REGISTRY "graceBlocks()(uint64)"     # 300
```

Formulas, in PLUR (10⁻¹⁶ BZZ). Sepolia has about 7200 blocks per day.

- **Initial charge** (`createVolume`): `lastPrice × graceBlocks × 2^depth`
- **Drain rate:** `lastPrice × 2^depth` per block, so `lastPrice × 2^depth × 7200` per day
- **Each top-up:** only what drained since the last one; at most the initial charge

**Size the allowance** for N days with a margin for price rises:

```
allowance ≥ initial charge + lastPrice × 2^depth × 7200 × N × 2
```

A larger allowance means fewer re-approvals; a smaller one limits what a compromised owner key can spend. When the allowance runs out, triggers skip with `PaymentFailed` and the volume coasts on its remaining balance, about one `graceBlocks` runway.

Gas for top-ups is paid by whoever calls `trigger`, not by the payer.

## 17. Testnet tokens

- **Sepolia ETH** for gas: the [Google Cloud Web3 Faucet](https://cloud.google.com/application/web3/faucet/ethereum/sepolia), or your RPC provider's faucet.
- **Sepolia BZZ (sBZZ)** for storage: there is no faucet. Ask in the [official Swarm Discord](https://docs.ethswarm.org/docs/references/faq/). A small [sBZZ/WETH Uniswap v3 pool](https://sepolia.etherscan.io/address/0x88A3f8097f091f457a3bf22e9BECAbCbE873eC1b) exists, but its liquidity is not guaranteed and it has no convenient frontend.

The token at `0x543dDb01…40db` is the BZZ that Sepolia's `PostageStamp` accepts. Other tokens named BZZ on Sepolia will not work.

## 18. Uploading to Swarm with Bee

The registry only keeps the batch alive. Uploading goes through a Bee node whose key is the volume's chunk signer.

1. **Run a Bee node on the Sepolia testnet.** A light node is enough. See the [Bee documentation](https://docs.ethswarm.org/docs/bee/installation) for testnet configuration and a Sepolia RPC endpoint.
2. **Fund the node with Sepolia ETH only.** It needs gas for its own transactions. It does not need BZZ for storage: the payer funds the batch through the registry.
3. **Use the chunk-signer key as the node's key.** Bee keeps its Ethereum key as a JSON keystore in its data directory (`keys/swarm.key`; check your Bee version's documentation). The node's address must equal the volume's `chunkSigner` exactly: Postage checks `batch.owner` against the stamp signer.
4. **Upload with the batch ID, not the volume ID.** Pass `batchId` from `getVolume` or the creating event in the `swarm-postage-batch-id` header of `/bzz`, `/bytes` or `/chunks`. Bee lists batches owned by its key under `GET /stamps` once it has synced the batch from the chain.

Do not call `increaseDepth` (Bee's "dilute") on a registry batch. The next trigger retires the volume as `DepthChanged`. Register the batch again afterwards if you want it maintained at the new depth.

## 19. Not in v2

Deliberately out of scope:

- **Ownership transfer.** The owner is permanent. A future version may add transfer with explicit acceptance by the recipient.
- **Depth changes.** Create a new volume of the size you need, or re-register after `increaseDepth` (see [§13](#13-retirement)).
- **Signer rotation.** The chunk signer is fixed per batch by Postage.
- **More than one payer per owner.** Use separate owner addresses.
- **A Safe `AllowanceModule` payment path, or EIP-712 off-chain authorization.** Payment is ERC-20 `approve` and `transferFrom`, and authorization is on-chain.
- **A shortcut for self-designation.** Profile A still needs both handshake steps.
- **On-chain spam prevention for `registerVolume`.** The active set can contain volumes that never fund. Keepers should expect skips.

## 20. References

- [Running a keeper](./KEEPERS.md): operating a keeper, including revert handling and monitoring.
- [GitHub repository](https://github.com/shtukaresearch/swarm-volume-registry): source, deployment records, and further design documentation.
- [`contracts/src/interfaces/`](../contracts/src/interfaces/): the Solidity interfaces.
- [`contracts/deployments/`](../contracts/deployments/): deployment records.
- [Swarm documentation](https://docs.ethswarm.org): Bee, uploads, postage batches.
- [`ethersphere/storage-incentives`](https://github.com/ethersphere/storage-incentives): `PostageStamp` and `PriceOracle` source.
- [Safe documentation](https://docs.safe.global): Protocol Kit, API Kit, Transaction Service.
