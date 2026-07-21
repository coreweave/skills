# Claude skills for CoreWeave Cloud

Ask Claude to deploy a CoreWeave Kubernetes Service (CKS) cluster, add a GPU
node pool, mint a scoped API token, deploy a vLLM inference endpoint, or load a
model into an object storage bucket — and it carries out the CoreWeave Cloud
workflow for you, one verified step at a time.

This repository is a library of
[Claude skills](https://docs.claude.com/en/docs/claude-code/skills) that teach
Claude Code how to operate CoreWeave Cloud products. Each skill is a complete,
tested workflow: it knows the prerequisites, runs the commands, and checks its
own work. You install the skills once, then describe what you want in plain
language — Claude picks the right skill and runs it.

You don't need to clone this repository. Claude Code installs the skills from a
plugin marketplace served directly from the repo.

## Prerequisites

Before you install the skills, make sure you have the following:

- [Claude Code](https://docs.claude.com/en/docs/claude-code/overview),
  installed and running.
- A CoreWeave Cloud account. Skills that call the CoreWeave API or `kubectl`
  walk you through creating the credentials they need.

## Install the skills

Skills are grouped into plugins, one plugin per CoreWeave product line. Install
the platform plugin plus whichever product-line plugins you use.

1. Add the CoreWeave marketplace. You do this once per machine:

   ```text
   /plugin marketplace add coreweave/skills
   ```

2. Install the plugins you want. The platform plugin provides shared building
   blocks — creating API tokens, fetching a kubeconfig, and checking workload
   health — as standalone skills you can run on their own. Install it alongside
   whichever product-line plugins you use:

   ```text
   /plugin install coreweave-platform-skills@coreweave-skills
   /plugin install coreweave-cks-skills@coreweave-skills
   /plugin install coreweave-storage-skills@coreweave-skills
   ```

3. Load the new skills into your current session:

   ```text
   /reload-plugins
   ```

After each install, Claude Code confirms with a line like `✓ Installed
coreweave-cks-skills. Run /reload-plugins to apply.` Running `/reload-plugins`
makes the skills available without restarting Claude Code.

To upgrade later, update the marketplace and reload:

```text
/plugin marketplace update coreweave-skills
/reload-plugins
```

## Use a skill

Once a plugin is installed, you can trigger a skill two ways:

- **Describe what you want.** Claude reads each skill's description and routes
  to the right one automatically. "Create a CKS cluster" triggers
  `cw-create-cluster`; "deploy a vLLM endpoint" triggers
  `cw-self-managed-inference`; "load my model into a bucket" triggers
  `cw-load-model-to-bucket`.
- **Invoke a skill by name** as `/<plugin>:<skill>` — for example,
  `/coreweave-cks-skills:cw-create-cluster`. Use this when you know exactly
  which workflow you want and don't want to rely on automatic routing.

Either way, the skill drives the workflow: it asks for the inputs it needs,
runs the commands, and verifies the result before reporting back.

### See it in action

The following animation shows a real session — adding the marketplace,
installing a plugin, reloading, then running
`/coreweave-cks-skills:cw-create-cluster` and watching Claude start the work.

![Installing and running a CoreWeave skill](assets/install-demo.gif)

## What's available

Each plugin covers one CoreWeave product line. Install a plugin and you get all
of its skills.

| Plugin | What it helps you do |
| --- | --- |
| `coreweave-platform-skills` | Foundational, standalone workflows: create a scoped API token, fetch a kubeconfig, and confirm a workload is running and healthy. |
| `coreweave-cks-skills` | CoreWeave Kubernetes Service (CKS): create a cluster and its VPC, add a GPU or CPU node pool, and deploy a self-managed vLLM inference service. |
| `coreweave-storage-skills` | Storage workflows: load a model into a CoreWeave object storage bucket. |
| `coreweave-networking-skills` | Networking workflows such as VPCs, load balancers, and ingress. In development — no skills yet. |
| `coreweave-sunk-skills` | SUNK (Slurm on Kubernetes) workflows such as provisioning Slurm clusters and submitting jobs. In development — no skills yet. |

Skills are added and updated over time. After you run `/plugin marketplace
update coreweave-skills`, any new skills in your installed plugins are picked
up automatically. To see what an installed plugin offers right now, ask Claude
"which CoreWeave skills do I have?"

## Get help

For documentation, support, or contributions, use the following resources:

- For CoreWeave product documentation, see
  [docs.coreweave.com](https://docs.coreweave.com).
- To report a problem with a skill or request a new one, open an issue in this
  repository or contact CoreWeave support.
- To contribute a skill, see [CONTRIBUTING.md](CONTRIBUTING.md).
