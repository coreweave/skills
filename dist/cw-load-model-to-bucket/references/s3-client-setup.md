# S3 client setup for CoreWeave AI Object Storage (CAIOS)

Reference material for `cw-load-model-to-bucket`. The main workflow uses the
AWS CLI; this file has the exhaustive install/config details for every
supported client, plus the endpoints, bucket naming rules, and the list of
Availability Zones that support CAIOS.

Two settings are non-negotiable for **every** client:

1. **Endpoint** — use one of the CoreWeave endpoints below (never the AWS
   default).
2. **Virtual-hosted addressing** — CAIOS does **not** support path-style
   addressing. Requests fail or hang if a client uses path-style.

When credentials come from `POST /v1/cwobject/access-key`, the live response
fields are exactly `accessKeyId` and `secretKey`. Map them to
`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`; do not use the AWS STS-style
names `AccessKeyId` or `SecretAccessKey` for this CoreWeave response.

## Endpoints

| Endpoint | When to use | Notes |
|----------|-------------|-------|
| `https://cwobject.com` | Outside a CoreWeave cluster (laptops, CI, other clouds) | Primary endpoint. Requires **TLS v1.3** — ensure the client and its OpenSSL support it. |
| `http://cwlota.com` | Inside a CoreWeave cluster (CKS/SUNK nodes) | The [LOTA](https://docs.coreweave.com/products/storage/object-storage/improving-performance/about-lota) endpoint; routes through the node-local cache for best throughput. |

---

## Install the tools

- **AWS CLI v2** — https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html
- **jq** — `brew install jq` / `apt-get install jq`
- **cwic — the CoreWeave Intelligent CLI (optional, preferred for key minting)**.
  Download the latest release from https://github.com/coreweave/cwic/releases
  and move the binary onto your `PATH`, then sign in once with
  `cwic auth login` (it stores the token in your local cwic config). A signed-in
  cwic mints an Object Storage access key in one command
  (`cwic cwobject token create`), replacing the Console-token + curl exchange.
- **Hugging Face CLI** — `pip install -U huggingface_hub` (provides `hf`).
  Note: the old `huggingface-cli` entry point is **deprecated and no longer
  works**, and the `[cli]` extra no longer exists — asking for it prints
  `does not provide the extra 'cli'` and installs nothing extra. On a
  Homebrew or system Python, `pip install` is refused outright by PEP 668. Use a
  scratch virtualenv rather than `--break-system-packages`, which mutates the
  machine's Python, and set `HF_HOME` so the cache and token land in the run
  directory rather than the customer's `~/.cache/huggingface`:
  ```bash
  export HF_HOME=<run-dir>/hf-home
  python3 -m venv <run-dir>/hf-venv
  source <run-dir>/hf-venv/bin/activate
  pip install -q -U huggingface_hub
  ```
  **Authenticate with `HF_TOKEN`, not `hf auth login`.** `HF_TOKEN` takes
  precedence over the on-disk token, so a login writes a redundant,
  lower-priority copy into the customer's token file — replacing their own login
  and changing which HF account their other tools act as, permanently. Setting the
  variable is sufficient; `hf auth whoami` confirms which identity is in use.
- **s3cmd** (optional) — `pip install s3cmd` / `brew install s3cmd`
- **s5cmd — CoreWeave fork (optional, recommended for large models)**. Do **not**
  use upstream `s5cmd`: it uses path-style addressing and is incompatible with
  CAIOS. Install a **pinned release** from
  https://github.com/coreweave/s5cmd/releases and verify its checksum before
  moving it onto `PATH` — never an unversioned "latest" binary:
  ```bash
  S5CMD_VERSION=2.3.0-acb67716                             # pinned; bump deliberately
  S5CMD_ASSET="s5cmd_${S5CMD_VERSION}_<OS-ARCH>.tar.gz"    # e.g. macOS-arm64, Linux-64bit
  # shasum -a 256 is macOS; use sha256sum on Linux.
  curl -fsSLO "https://github.com/coreweave/s5cmd/releases/download/v${S5CMD_VERSION}/${S5CMD_ASSET}"
  curl -fsSLO "https://github.com/coreweave/s5cmd/releases/download/v${S5CMD_VERSION}/s5cmd_checksums.txt"
  grep -F "$S5CMD_ASSET" s5cmd_checksums.txt | shasum -a 256 -c - \
    && tar xzf "$S5CMD_ASSET" s5cmd \
    && chmod +x s5cmd && sudo mv s5cmd /usr/local/bin/
  s5cmd version
  ```
  The `&&` chain is what makes this fail closed: a mismatched digest — or an
  asset name absent from `s5cmd_checksums.txt`, which leaves `shasum` nothing to
  check — exits non-zero and stops before the binary is unpacked or installed.
  Stop and tell the customer if the checksum line does not end in `OK`; do not
  install an artifact that fails verification.

  The checksums file ships from the same GitHub release as the binary, so it
  proves integrity (the download was not corrupted or tampered with in transit),
  not provenance — anyone who could replace the asset could replace the
  checksums too. It is trust-on-first-use against a pinned release, not an
  independent trust root. Verifying release signatures is tracked separately
  (APPSEC-3965).

  The fork defaults to virtual-hosted addressing for CAIOS and safely replaces
  any existing `s5cmd` install (other S3 backends are unaffected).

  **`s5cmd` being on `PATH` is not evidence that it is the fork** — an upstream
  build answers `s5cmd version` just as happily and then fails only on a large
  upload, after the bucket exists. Check the build before relying on it:
  ```bash
  s5cmd version                     # look for a CoreWeave/coreweave build marker
  command -v s5cmd | xargs strings 2>/dev/null | grep -i -m3 coreweave
  ```
  If neither identifies the fork, treat `s5cmd` as unavailable and use
  `aws s3 sync`.

---

## Configure the AWS CLI

### Option A — flags plus one isolated setting (used by the workflow)

Keep CoreWeave settings entirely out of the machine's `~/.aws`, because real
workstations already carry profiles for other AWS accounts, other S3-compatible
providers, and sometimes a second CoreWeave org — settings that must survive the
run untouched.

Most of what CAIOS needs has a per-invocation form, so it never reaches a file:

| Setting | Per-invocation form |
|---------|---------------------|
| Endpoint | `--endpoint-url https://cwobject.com` |
| Region / AZ | `--region <AZ>` |
| Credentials | `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` in the environment |

**One setting has no flag and no environment variable: `s3.addressing_style`.**
It is also mandatory, and for a non-obvious reason — in the AWS SDK, specifying a
custom endpoint makes S3 default to *path-style* addressing, which CAIOS does not
support. So pointing the client at `cwobject.com` selects the one addressing mode
CAIOS rejects, and a config file is the only way to override it:

```bash
export AWS_CONFIG_FILE=<run-dir>/aws-config     # isolated; not ~/.aws
aws configure set profile.cw-byow.s3.addressing_style virtual
```

Then every command carries its own pin:

```bash
env -u AWS_SESSION_TOKEN -u AWS_SECURITY_TOKEN -u AWS_ENDPOINT_URL \
  -u AWS_ENDPOINT_URL_S3 -u AWS_REGION -u AWS_DEFAULT_REGION AWS_PROFILE=cw-byow \
  aws s3 ls --endpoint-url https://cwobject.com --region <AZ>
```

Two details are easy to get wrong:

- **`AWS_PROFILE=…`, not `--profile …`.** Passing `--profile` on the command line
  makes the SDK drop the environment credential provider ("an explicitly provided
  profile will negate an EnvProvider"), so credentials held only in the
  environment are ignored and the command fails with `Unable to locate
  credentials` — or silently falls through to an instance role. Setting
  `AWS_PROFILE` keeps the environment provider in the chain, so the run's
  credentials sign the request while the profile still supplies
  `addressing_style`.
- **`env -u` applies to that one child process** and does not modify the caller's
  shell. Each variable is stripped for a reason: a session token left from an AWS
  SSO login is attached to the CoreWeave key and breaks signing; an ambient
  endpoint or region would take effect if the corresponding flag were ever
  omitted, sending CoreWeave credentials to another provider or creating the
  bucket in the wrong zone.

`AWS_CONFIG_FILE` must be set in any shell that runs `aws` — without it the CLI
looks in `~/.aws`, where `cw-byow` deliberately does not exist, so the command
fails loudly instead of acting as some other account. For a setup that outlives
the run, put the file somewhere durable such as `~/.coreweave/` rather than a
scratch directory.

### Option B — dedicated profile in the standard AWS files (only on request)

Only when the customer explicitly wants a persistent profile in their real
`~/.aws`. Check what already exists first — `aws configure list-profiles` —
and pick a free name; on multi-org machines `cw` is often already taken by
another CoreWeave account. Write it with `aws configure set` (which rewrites
the files safely), **never by appending text blocks** — an appended duplicate
section silently hijacks the existing profile, because the last definition of
each key wins. The resulting profile looks like:

```ini
# ~/.aws/config
[profile <NAME>]
region = <AZ>
endpoint_url = https://cwobject.com
s3 =
    addressing_style = virtual
```

```ini
# ~/.aws/credentials
[<NAME>]
aws_access_key_id = <ACCESS-KEY-ID>
aws_secret_access_key = <SECRET-ACCESS-KEY>
```

Then every command takes `--profile <NAME>` (or `export AWS_PROFILE=<NAME>`).
Inline `endpoint_url` in a profile requires a recent AWS CLI v2.

### Option C — run the whole workflow in a container

On a locked-down or heavily-configured workstation, running the client inside a
container gives a guaranteed-empty environment. It is not the default here for
three reasons: it does not answer *which organization* should act, so it prevents
none of the multi-org mistakes; the `cwic` session lives in the host's config, so
you end up mounting that ambient state back in; and the model weights need either
a volume mount or a second download inside the container. Prefer per-command
flags. Reach for a container only when the customer's environment is genuinely
hostile and they ask for it.

---

## Configure s3cmd

**Do not run a bare `s3cmd --configure`.** It writes `~/.s3cfg` wholesale, which
overwrites whatever the customer already has there for another provider or another
CoreWeave org — the same mistake as pasting a profile into `~/.aws`. Point it at a
config file in the run directory instead, with `-c`:

```bash
s3cmd -c <run-dir>/s3cfg --configure
```

| Prompt | Value |
|--------|-------|
| Access Key | Your CAIOS access key ID |
| Secret Key | Your CAIOS secret key |
| Default Region | The CoreWeave Availability Zone you resolved (see below) |
| S3 Endpoint | `cwobject.com` (or `cwlota.com` inside a cluster) |
| DNS-style bucket+hostname template | `%(bucket)s.cwobject.com` (or `%(bucket)s.cwlota.com`) |
| Use HTTPS protocol | `True` for the primary endpoint |

Leave the rest at defaults. Pass `-c <run-dir>/s3cfg` on **every** subsequent
call — without it s3cmd falls back to `~/.s3cfg` and may act as a different
account:

```bash
s3cmd -c <run-dir>/s3cfg mb --bucket-location=<AZ> s3://<BUCKET-NAME>   # create a bucket
s3cmd -c <run-dir>/s3cfg put <LOCAL-FILE> s3://<BUCKET-NAME>            # upload an object
s3cmd -c <run-dir>/s3cfg ls s3://<BUCKET-NAME>/                         # list objects
```

### `cwic cwobject` reads the same file

The `cwic cwobject` subcommands take their S3 credentials from `~/.s3cfg` or from
ambient environment variables — **not** from the `cwic auth` session, which only
covers `token create`. On a machine with an existing `~/.s3cfg` for another org,
`cwic cwobject list` silently reports that other account's buckets. Always pass
the run's file explicitly:

```bash
cwic cwobject list --config <run-dir>/s3cfg
cwic cwobject bucket describe <BUCKET-NAME> --config <run-dir>/s3cfg
```

---

## Configure s5cmd (CoreWeave fork)

`s5cmd` reads credentials from the standard AWS chain, so it reuses the
environment credentials and the isolated `cw-byow` profile. Pass the endpoint
explicitly; the fork supplies virtual-hosted addressing:

```bash
# Via the isolated cw-byow profile:
env -u AWS_SESSION_TOKEN -u AWS_SECURITY_TOKEN \
  AWS_PROFILE=cw-byow AWS_CONFIG_FILE=<run-dir>/aws-config \
  s5cmd --endpoint-url https://cwobject.com ls s3://<BUCKET-NAME>/

# Or via environment variables:
export AWS_ACCESS_KEY_ID=<ACCESS-KEY-ID>
export AWS_SECRET_ACCESS_KEY=<SECRET-ACCESS-KEY>
export AWS_REGION=US-EAST-04A
s5cmd --endpoint-url https://cwobject.com cp <LOCAL-DIR>/ s3://<BUCKET-NAME>/<PREFIX>/
```

Inside a CoreWeave cluster, use `--endpoint-url http://cwlota.com`.

---

## Bucket naming rules

Bucket names must be **globally unique** across all CAIOS customers and must
satisfy all of:

- **Length:** 3–63 characters.
- **Characters:** lowercase letters (`a-z`), digits (`0-9`), and hyphens (`-`)
  only. No dots, uppercase, underscores, or spaces.
- **Start/end:** must begin and end with a letter or digit (no leading/trailing
  hyphen).
- **Prohibited prefix:** cannot start with `xn--`.
- **Reserved:** cannot start with `cw-`, `vip-`, or `log-stitcher-ch-`, and
  cannot be the exact name `int`. CoreWeave reserves these.

---

## Availability Zones that support CAIOS

Create buckets in one of these AZs (used as the S3 "region" /
`LocationConstraint`). This list changes over time — confirm against
[Create a bucket](https://docs.coreweave.com/products/storage/object-storage/buckets/create-bucket)
if an AZ is rejected.

| Super Region | Availability Zones |
|--------------|--------------------|
| US-CENTRAL | `US-CENTRAL-05A`, `US-CENTRAL-06A`, `US-CENTRAL-07A`, `US-CENTRAL-08A`, `US-CENTRAL-08B` |
| US-EAST | `US-EAST-01A`, `US-EAST-02A`, `US-EAST-03A`, `US-EAST-04A`, `US-EAST-04B`, `US-EAST-06A`, `US-EAST-08A`, `US-EAST-13A`, `US-EAST-14A`, `US-EAST-15A`, `US-EAST-17A` |
| US-WEST | `RNO2A`, `US-WEST-01A`, `US-WEST-04A`, `US-WEST-09B`, `US-WEST-10A` |
| CA-EAST | `CA-EAST-01A` |
| EU-NORTH | `EU-NORTH-05A` |
| EU-SOUTH | `EU-SOUTH-03B`, `EU-SOUTH-04A` |

---

## Sources

- Create a bucket: https://docs.coreweave.com/products/storage/object-storage/buckets/create-bucket
- Manage buckets (configure tools): https://docs.coreweave.com/products/storage/object-storage/buckets/manage-buckets
- Configure endpoints: https://docs.coreweave.com/products/storage/object-storage/using-object-storage/configure-endpoints
- Migrate data with s5cmd: https://docs.coreweave.com/products/storage/object-storage/migrate-data
- Manage access keys: https://docs.coreweave.com/products/storage/object-storage/auth-access/manage-access-keys/about
