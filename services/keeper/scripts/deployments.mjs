// Prints the envs in wrangler.jsonc as a JSON array: the deployments
// keeper-deploy.yml ships, one Worker each.
import { experimental_readRawConfig } from "wrangler";

const { rawConfig } = experimental_readRawConfig({ config: "wrangler.jsonc" });
console.log(JSON.stringify(Object.keys(rawConfig.env ?? {})));
