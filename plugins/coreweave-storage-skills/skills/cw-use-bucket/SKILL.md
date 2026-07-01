---
name: cw-use-bucket
description: >
  Provides ready-to-paste commands for copying files to and from CoreWeave
  AI Object Storage buckets using the AWS CLI or s5cmd. Detects whether the
  customer is inside a CoreWeave cluster (LOTA endpoint) or outside (default
  endpoint) and generates self-contained commands that inline credentials and
  endpoint — no pre-existing environment variables or config files required.
  Use this skill whenever a customer mentions uploading or downloading files
  to a bucket, copying data to object storage, syncing a directory to S3,
  transferring training data or checkpoints, or using aws s3 or s5cmd with
  CoreWeave storage — even if they don't say "use bucket" explicitly. Also
  use when someone asks how to move data into or out of CAIOS, or wants
  example S3 commands for CoreWeave.
---

# Copy files to and from a CoreWeave Object Storage bucket

You are helping a CoreWeave customer move data to or from an AI Object Storage (CAIOS) bucket. You will determine their location (inside or outside a CoreWeave cluster), their preferred tool (`aws` CLI or `s5cmd`), and generate complete, paste-ready commands.

**Do as much as possible before asking.** Probe the local environment silently for credentials, tools, bucket names, and location. Only ask the customer for information you cannot determine from local state.

---

## Before you start — silent environment probe

Run all of these checks silently before saying anything to the customer. Do not ask for permission to probe.

### 1. Check for credentials

Search for existing credentials in order of precedence:

```bash
# Environment variables
echo "AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID:-(not set)}"
echo "AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY:+(set)}"

# CoreWeave-specific config
cat ~/.coreweave/cw.credentials 2>/dev/null
cat ~/.coreweave/cw.config 2>/dev/null

# Standard AWS config
cat ~/.aws/credentials 2>/dev/null
cat ~/.aws/config 2>/dev/null
```

### 2. Check which tools are installed

```bash
aws --version 2>/dev/null
s5cmd version 2>/dev/null
```

### 3. Detect location (inside vs. outside CoreWeave)

```bash
# Check if LOTA is reachable (indicates running inside CoreWeave)
curl -s --connect-timeout 2 http://cwlota.com/ 2>/dev/null && echo "INSIDE_CW=true" || echo "INSIDE_CW=false"

# Check for Kubernetes service account (another indicator of running inside a pod)
test -f /var/run/secrets/kubernetes.io/serviceaccount/token && echo "IN_K8S_POD=true" || echo "IN_K8S_POD=false"
```

### 4. List existing buckets (if credentials work)

```bash
AWS_SHARED_CREDENTIALS_FILE=~/.coreweave/cw.credentials \
AWS_CONFIG_FILE=~/.coreweave/cw.config \
aws s3 ls --profile cw 2>&1
```

Or with environment variables:

```bash
aws s3 ls --endpoint-url https://cwobject.com --region us-east-1 2>&1
```

### 5. Present findings and fill gaps

After probing, present what you found and only ask for what's missing. For example:

> "Here's what I found:
> - **Credentials**: found in `~/.coreweave/cw.credentials` (profile `cw`)
> - **Tools**: `aws` CLI v2.x installed, `s5cmd` not found
> - **Location**: outside CoreWeave (endpoint: `https://cwobject.com`)
> - **Buckets**: `acme-training-data`, `acme-checkpoints`
>
> Which bucket do you want to copy to/from, and what's the local path?"

If credentials are missing, point them to the `cw-create-bucket` skill or give concise instructions:

> "I didn't find any CoreWeave Object Storage credentials. You need an access key:
> 1. Go to **console.coreweave.com** → **Object Storage** → **Access Keys** → **Create Key**
> 2. Copy the Access Key ID and Secret Key (secret is shown only once)
> 3. Paste them here and I'll configure everything."

Once they provide credentials, configure them locally:

```bash
mkdir -p ~/.coreweave

AWS_SHARED_CREDENTIALS_FILE=~/.coreweave/cw.credentials aws configure --profile cw
# Prompt: Access Key ID, Secret Key, region: us-east-1, output: json

AWS_CONFIG_FILE=~/.coreweave/cw.config aws configure set endpoint_url https://cwobject.com --profile cw
AWS_CONFIG_FILE=~/.coreweave/cw.config aws configure set s3.addressing_style virtual --profile cw
```

---

## Step 1 — Determine the tool

If both `aws` and `s5cmd` are installed, default to:
- **s5cmd** for bulk/large transfers (parallelizes automatically)
- **aws** for simple single-file operations

If only one is installed, use that one without asking.

If neither is installed, recommend `aws` CLI (more widely applicable) and provide install instructions:
- https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html

For **s5cmd** (CoreWeave fork), if the customer wants it:
- **macOS**: `brew install peak/tap/s5cmd`
- **Go**: `go install github.com/peak/s5cmd/v2@master`
- **Linux**: download the binary from https://github.com/coreweave/s5cmd/releases and add to PATH

---

## Step 2 — Generate commands

Once you have credentials, bucket name, location, and tool, generate the commands. **Substitute actual values into every command** — never show template placeholders to the customer.

The endpoint depends on location:
- **Inside CoreWeave**: `http://cwlota.com` (LOTA — faster, caches reads)
- **Outside CoreWeave**: `https://cwobject.com`

Determine which credential-passing method to use based on how credentials are stored:
- **CoreWeave config files**: use `AWS_SHARED_CREDENTIALS_FILE=~/.coreweave/cw.credentials AWS_CONFIG_FILE=~/.coreweave/cw.config ... --profile cw`
- **Environment variables**: credentials are already available, just pass `--endpoint-url` and `--region`
- **Standard AWS config with a CW profile**: use `--profile <profile-name>`

Present only the commands relevant to the customer's operation (upload, download, sync) — not the entire reference sheet. Ask for the specific local path and remote path before generating the final command.

---

### AWS CLI commands

#### Copy a local file to the bucket

```bash
AWS_ACCESS_KEY_ID=ACCESS_KEY_ID \
AWS_SECRET_ACCESS_KEY=SECRET_ACCESS_KEY \
aws s3 cp LOCAL_FILE s3://BUCKET/REMOTE_PATH \
  --endpoint-url ENDPOINT \
  --region us-east-1
```

#### Copy a file from the bucket to local

```bash
AWS_ACCESS_KEY_ID=ACCESS_KEY_ID \
AWS_SECRET_ACCESS_KEY=SECRET_ACCESS_KEY \
aws s3 cp s3://BUCKET/REMOTE_PATH LOCAL_FILE \
  --endpoint-url ENDPOINT \
  --region us-east-1
```

#### Copy a directory (recursive)

```bash
AWS_ACCESS_KEY_ID=ACCESS_KEY_ID \
AWS_SECRET_ACCESS_KEY=SECRET_ACCESS_KEY \
aws s3 cp LOCAL_DIR s3://BUCKET/REMOTE_DIR --recursive \
  --endpoint-url ENDPOINT \
  --region us-east-1
```

#### Sync a directory to the bucket (only transfers changed files)

```bash
AWS_ACCESS_KEY_ID=ACCESS_KEY_ID \
AWS_SECRET_ACCESS_KEY=SECRET_ACCESS_KEY \
aws s3 sync LOCAL_DIR s3://BUCKET/REMOTE_DIR \
  --endpoint-url ENDPOINT \
  --region us-east-1
```

#### Sync from the bucket to local

```bash
AWS_ACCESS_KEY_ID=ACCESS_KEY_ID \
AWS_SECRET_ACCESS_KEY=SECRET_ACCESS_KEY \
aws s3 sync s3://BUCKET/REMOTE_DIR LOCAL_DIR \
  --endpoint-url ENDPOINT \
  --region us-east-1
```

#### List bucket contents

```bash
AWS_ACCESS_KEY_ID=ACCESS_KEY_ID \
AWS_SECRET_ACCESS_KEY=SECRET_ACCESS_KEY \
aws s3 ls s3://BUCKET/ \
  --endpoint-url ENDPOINT \
  --region us-east-1
```

#### List bucket contents (recursive, with sizes)

```bash
AWS_ACCESS_KEY_ID=ACCESS_KEY_ID \
AWS_SECRET_ACCESS_KEY=SECRET_ACCESS_KEY \
aws s3 ls s3://BUCKET/ --recursive --human-readable --summarize \
  --endpoint-url ENDPOINT \
  --region us-east-1
```

---

### s5cmd commands

s5cmd accepts credentials via environment variables and the endpoint via `--endpoint-url`.

#### Copy a local file to the bucket

```bash
AWS_ACCESS_KEY_ID=ACCESS_KEY_ID \
AWS_SECRET_ACCESS_KEY=SECRET_ACCESS_KEY \
s5cmd --endpoint-url ENDPOINT \
  cp LOCAL_FILE s3://BUCKET/REMOTE_PATH
```

#### Copy a file from the bucket to local

```bash
AWS_ACCESS_KEY_ID=ACCESS_KEY_ID \
AWS_SECRET_ACCESS_KEY=SECRET_ACCESS_KEY \
s5cmd --endpoint-url ENDPOINT \
  cp s3://BUCKET/REMOTE_PATH LOCAL_FILE
```

#### Copy a directory (recursive — automatic with wildcard)

```bash
AWS_ACCESS_KEY_ID=ACCESS_KEY_ID \
AWS_SECRET_ACCESS_KEY=SECRET_ACCESS_KEY \
s5cmd --endpoint-url ENDPOINT \
  cp 'LOCAL_DIR/*' s3://BUCKET/REMOTE_DIR/
```

#### Copy an entire bucket prefix to local

```bash
AWS_ACCESS_KEY_ID=ACCESS_KEY_ID \
AWS_SECRET_ACCESS_KEY=SECRET_ACCESS_KEY \
s5cmd --endpoint-url ENDPOINT \
  cp 's3://BUCKET/REMOTE_DIR/*' LOCAL_DIR/
```

#### Sync a directory to the bucket

```bash
AWS_ACCESS_KEY_ID=ACCESS_KEY_ID \
AWS_SECRET_ACCESS_KEY=SECRET_ACCESS_KEY \
s5cmd --endpoint-url ENDPOINT \
  sync LOCAL_DIR/ s3://BUCKET/REMOTE_DIR/
```

#### Sync from the bucket to local

```bash
AWS_ACCESS_KEY_ID=ACCESS_KEY_ID \
AWS_SECRET_ACCESS_KEY=SECRET_ACCESS_KEY \
s5cmd --endpoint-url ENDPOINT \
  sync 's3://BUCKET/REMOTE_DIR/*' LOCAL_DIR/
```

#### List bucket contents

```bash
AWS_ACCESS_KEY_ID=ACCESS_KEY_ID \
AWS_SECRET_ACCESS_KEY=SECRET_ACCESS_KEY \
s5cmd --endpoint-url ENDPOINT \
  ls s3://BUCKET/
```

#### List recursively

```bash
AWS_ACCESS_KEY_ID=ACCESS_KEY_ID \
AWS_SECRET_ACCESS_KEY=SECRET_ACCESS_KEY \
s5cmd --endpoint-url ENDPOINT \
  ls 's3://BUCKET/*'
```

#### High-parallelism transfer (tune worker count)

```bash
AWS_ACCESS_KEY_ID=ACCESS_KEY_ID \
AWS_SECRET_ACCESS_KEY=SECRET_ACCESS_KEY \
s5cmd --endpoint-url ENDPOINT --numworkers 64 \
  cp 's3://BUCKET/REMOTE_DIR/*' LOCAL_DIR/
```

---

## How to present commands to the customer

**Do not show the template placeholders above.** Substitute the customer's actual values into every command before presenting it. Use whichever credential-passing method matches their setup (config files with `--profile`, environment variables, or inline).

For example, if credentials are in `~/.coreweave/cw.credentials` and the customer is outside CoreWeave:

```bash
AWS_SHARED_CREDENTIALS_FILE=~/.coreweave/cw.credentials \
AWS_CONFIG_FILE=~/.coreweave/cw.config \
aws s3 cp ./my-dataset s3://acme-training-data/datasets/my-dataset --recursive \
  --profile cw
```

---

## Go CLI tools and TLS proxy conflicts

Go-based CLI tools (such as `cwic`, `terraform`, and other Go binaries) may fail with a TLS certificate verification error when running inside Claude Code Remote sessions:

```
tls: failed to verify certificate: x509: OSStatus -26276
```

**Root cause:** Claude Code Remote routes outbound traffic through a local HTTP proxy (`HTTPS_PROXY=http://localhost:<port>`). Go on macOS delegates TLS certificate verification to the macOS Security framework, which fails when the connection goes through this CONNECT-tunnel proxy. Other tools (curl, Python, AWS CLI) are unaffected because they use their own TLS stacks.

**Workaround:** Unset the proxy environment variables before running any Go-based CLI tool:

```bash
HTTPS_PROXY="" HTTP_PROXY="" https_proxy="" http_proxy="" cwic <command>
```

**When to apply:** If you detect `HTTPS_PROXY` or `https_proxy` pointing to `localhost` and a Go CLI tool fails with an `x509` or `OSStatus` TLS error, automatically retry the command with the proxy variables unset. System DNS still works without the proxy, so bypassing it is safe for direct connections.

---

## Common mistakes

**Using https with LOTA**
The LOTA endpoint is `http://cwlota.com` (HTTP, not HTTPS). Using `https://cwlota.com` will fail with a TLS error.

**Using LOTA from outside CoreWeave**
LOTA is only available from within a CoreWeave cluster. From an external machine, use `https://cwobject.com`.

**Forgetting `--recursive` with aws s3 cp for directories**
Without `--recursive`, `aws s3 cp` only copies a single file. s5cmd handles this differently — use a wildcard pattern (`'dir/*'`).

**Quoting s5cmd wildcards**
s5cmd wildcard patterns must be quoted to prevent shell expansion: `'s3://bucket/*'`, not `s3://bucket/*`.

**Region errors**
CoreWeave doesn't use AWS regions, but the AWS CLI requires one. Use `--region us-east-1` as a placeholder — it's ignored by the CoreWeave endpoint but satisfies the CLI.

**Missing org access policy**
If uploads or downloads fail with access-denied errors and the credentials are correct, the organization may be missing an Object Storage access policy. See the `cw-create-bucket` skill for setup.

**LOTA only accelerates reads**
LOTA caches GET requests. Uploads go to the backend at the same speed as the standard endpoint. This is expected — don't troubleshoot upload speed differences between endpoints.

---

## References

- `references/s5cmd-quick-reference.md` — s5cmd command cheat sheet and tuning options.
- [CoreWeave Object Storage docs](https://docs.coreweave.com/products/storage/object-storage)
- [Manage objects](https://docs.coreweave.com/products/storage/object-storage/using-object-storage/manage-objects)
- [About LOTA](https://docs.coreweave.com/products/storage/object-storage/improving-performance/about-lota)
- [CoreWeave s5cmd fork](https://github.com/coreweave/s5cmd)
