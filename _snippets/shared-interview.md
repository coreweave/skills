# Skill Interview

This interview configures the skill suite for the user's local environment.
Each section is **independently runnable** — run only the section(s) needed to
fill in missing config keys. Completed sections are never re-run unless the user
resets a key to `_not set_`.

After completing any section, write the configured values to
`<project-root>/.skillconfig/config.md`, where `<project-root>` is the directory
`claude` was invoked from — **not** relative to this skill file's location. Confirm
what was written to the user before proceeding.

> **Note — config sharing depends on invocation directory.** All skills invoked
> from the same directory share one config file, so the interview runs once and
> every skill benefits. This is the expected pattern for customers who download
> the full skill suite to one place. If `claude` is ever run from different
> directories (or skills are installed in separate locations), each directory gets
> its own config and the interview will re-run. For the standard install case this
> isn't a concern, but it's worth knowing if setup ever seems to repeat
> unexpectedly.

---

## Section: kubectl

**Configures:** `kubectl_client_version`

**Goal:** Confirm that `kubectl` is installed and record its client version so
later skills can skip this check.

**Auto-detect:**

```bash
kubectl version --client --output=yaml 2>/dev/null
```

1. If the command succeeds, extract the `gitVersion` field (e.g. `v1.28.4`)
   and store it as `kubectl_client_version`.
2. If the command is not found or fails, guide the user to install `kubectl`
   before continuing (see below).

**If auto-detected,** confirm before writing:
> I found `kubectl` client version **[version]** — I'll record that and move on.
> Does that look right?

**If not found,** tell the user:
> `kubectl` isn't installed or isn't on your PATH. Install it before this
> skill can run:
>
> - **macOS:** `brew install kubectl`
> - **Linux:** follow https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/
> - **Windows:** `winget install Kubernetes.kubectl`
>
> Once installed, re-open this session and I'll pick up where we left off.

**Write to `<project-root>/.skillconfig/config.md`:**
- `kubectl_client_version` (e.g. `v1.28.4`)

---

## Section: CoreWeave CLI

**Configures:** `coreweave_cli_version`

**Goal:** Confirm that the `coreweave` CLI is installed and working.

**Auto-detect:**

```bash
coreweave version 2>/dev/null
```

1. If the command succeeds, extract and store the version string as
   `coreweave_cli_version`.
2. If the command is not found or fails, guide the user to install it.

**If auto-detected,** confirm:
> I found the CoreWeave CLI version **[version]** — recording that and moving on.
> Sound right?

**If not found,** tell the user:
> The `coreweave` CLI isn't installed or isn't on your PATH. Install it:
>
> - Follow the instructions at
>   https://docs.coreweave.com/coreweave-kubernetes/coreweave-cli
>
> Once installed, run `coreweave login` to authenticate, then re-open this
> session.

**Write to `<project-root>/.skillconfig/config.md`:**
- `coreweave_cli_version` (e.g. `0.5.2`)

---

## Section: CoreWeave Authentication

**Configures:** `cw_auth_token_env`

**Goal:** Determine which environment variable holds the CoreWeave API token
so skills can reference it consistently. The *name* of the variable is stored
in config — never the token value itself.

**Auto-detect** (check in order, stop at first match):

1. `$CW_API_TOKEN` is set and non-empty → use `CW_API_TOKEN`
2. `$COREWEAVE_API_TOKEN` is set and non-empty → use `COREWEAVE_API_TOKEN`
3. Nothing found → ask

**If auto-detected,** confirm:
> I found your CoreWeave API token in `$[ENV_VAR_NAME]` — I'll use that env var
> name going forward. Is that the right one?

**If nothing detected,** ask:
> I couldn't find a CoreWeave API token in the usual env vars. What's the
> name of the environment variable where you store it?
> - `CW_API_TOKEN` (default)
> - `COREWEAVE_API_TOKEN`
> - Something else — please tell me the variable name

> Note: skills will reference the token by reading `$[ENV_VAR_NAME]` at
> runtime. Make sure it's exported in your shell before running any skill.

**Write to `<project-root>/.skillconfig/config.md`:**
- `cw_auth_token_env` (the variable *name*, e.g. `CW_API_TOKEN` — not the token value)

---

## Section: Helm (optional)

**Configures:** `helm_version`

**Goal:** Confirm that `helm` is installed if any skills in the suite require it.
Only run this section if a skill explicitly lists `helm_version` as a required key.

**Auto-detect:**

```bash
helm version --short 2>/dev/null
```

1. If the command succeeds, store the version string as `helm_version`.
2. If not found, guide the user to install it.

**If auto-detected,** confirm:
> Found Helm **[version]** — recording that. Good to go?

**If not found,** tell the user:
> `helm` isn't installed. Install it:
>
> - **macOS:** `brew install helm`
> - **Linux/Windows:** https://helm.sh/docs/intro/install/

**Write to `<project-root>/.skillconfig/config.md`:**
- `helm_version` (e.g. `v3.14.0`)

---

## Completion

After writing any section, confirm to the user with a summary of what changed:

> **Configuration updated** (`<project-root>/.skillconfig/config.md`):
>
> | Key                    | Value          |
> |------------------------|----------------|
> | [key 1]                | [value 1]      |
> | [key 2]                | [value 2]      |
>
> [Any remaining keys that are still `_not set_` and required by the active
> skill will be configured the first time a skill that needs them is used.]
>
> Ready. What would you like me to do?

Only show keys that were written in this session. Do not dump the full config file.
