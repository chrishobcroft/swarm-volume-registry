# Releasing the keeper

How a contract release gets the keeper the maintainers run. Deploying the contract is the repository's [`RELEASING.md`](../../RELEASING.md) Part A, which also defines release names; this is its Part C.

Each registry deployment gets its own keeper Worker and wallet, and an existing keeper is never repointed. The new deployment's env replaces its predecessor's in `services/keeper/wrangler.jsonc`; the predecessor's Worker is not touched and keeps running whatever it was last deployed with until it is deleted out of band (`pnpm exec wrangler delete --name <worker>`). This is how the keeper service the maintainers run is operated, not a rule for anyone else running a keeper.

[`keeper-deploy.yml`](../../.github/workflows/keeper-deploy.yml) deploys every env in `wrangler.jsonc` on each merge to `main` that touches the keeper, after `keeper-ci` passes; it can also be run by hand from `main`. It uploads code and vars only. Secrets are set in Cloudflare by hand, once per Worker, and a deploy fails while any of them is missing. A new release's keeper therefore gets its wallet and secrets (C2, C3) before the PR that adds its env (C1) merges (C4).

Run commands from `services/keeper/`, with `LABEL` set to the deployment label, `<network>-<release>` as `contracts/deployments/` names it:

```sh
LABEL=<network>-<release>    # sepolia-v2-rc4
```

## C1. Switch the keeper to the release

In `wrangler.jsonc`, replace the env with one keyed by the new label:

- `name` and `DEPLOYMENT_NAME` are both `keeper-cloudflare-$LABEL`;
- `CHAIN_ID` and `REGISTRY_ADDRESS` are the deployment record's.

Add the outgoing Worker, its release and the commit it was last deployed from to the list of retired Workers at the top of the file: that commit is where to redeploy it from, if it ever has to be. Each Worker version's message is the commit it was deployed from (`pnpm exec wrangler deployments list --name <worker>`). In `package.json`, point `dev` and `check:deploy` at the new env.

Then bring the keeper's code up to the release. Diff the contract against the previous release tag (`git diff <previous-tag> <tag> -- contracts/src`), and change whatever the keeper needs: the hand-written ABI in `src/keeper/abi.ts`, and anything that depends on how the contract behaves, such as what a revert or an event means. `test/config.test.ts` fails until the env names a committed deployment record and the ABI matches that record's. It holds every env to the same code, so if the change leaves another network's env, such as mainnet's, out of step with its own record, remove that env too and list its Worker as retired. Its Worker keeps running what it was last deployed with, and CI no longer redeploys it until a release for that network brings it back in step. Regenerate the binding types and check everything:

```sh
pnpm types
pnpm test
pnpm typecheck
pnpm check:deploy
```

Commit and open a PR to `main`. Do not merge it until C3 is done.

## C2. Create and fund the wallet

```sh
python3 scripts/new_wallet.py "$LABEL" --rpc-url "$RPC_URL" --funder <account> --value <ether>
```

This creates the keystore account `keeper-cloudflare-$LABEL`, prompting for its new password, and funds it from the keystore account named by `--funder`, prompting for that keystore's password. The funder is any account in your Foundry keystore that holds the chain's native token; the maintainers use the contract deployer, `volume-registry-deployer`. Add `--password-prompt` to enter both in desktop dialogs. It sends the native token only: the keeper pays gas and never holds BZZ. Fund it well above the env's `MIN_BALANCE_WEI`, the level at which runs start to warn. The script refuses a label with no deployment record, an RPC on another chain, and an account that already exists. With `--existing` it instead requires the account and only funds it: rerun that way when a run created the wallet but failed to fund it, or to top the wallet up.

## C3. Set the secrets

From the C1 branch, so that `--env` names the new Worker. The key comes straight from the keystore:

```sh
umask 077
{
  printf 'PRIVATE_KEY=%s\n' "$(cast wallet private-key --account "keeper-cloudflare-$LABEL")"
  printf 'RPC_URL=https://…,https://…\nTELEGRAM_BOT_TOKEN=…\nTELEGRAM_CHAT_ID=-100…\n'
} > ".secrets.$LABEL"
pnpm exec wrangler secret bulk ".secrets.$LABEL" --env "$LABEL"
rm ".secrets.$LABEL"
```

The Worker does not exist yet, so `wrangler` creates a placeholder under its name, with no code and no cron trigger, to hold the secrets. Run it under your own `wrangler login`: the CI token can redeploy a Worker but not create one.

## C4. Merge

Merge the C1 PR. `keeper-deploy` replaces the placeholder with the keeper.

## C5. Check the first run

`GET /health` returns the wallet address, which must be the one C2 created:

```sh
curl "https://keeper-cloudflare-$LABEL.<subdomain>.workers.dev/health"
pnpm exec wrangler tail --env "$LABEL" --format json
```

Wait for one cron tick and confirm the run reports `ok` against the new registry address.
