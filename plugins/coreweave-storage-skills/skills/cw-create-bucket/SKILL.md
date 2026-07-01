---
name: cw-create-bucket
description: >
  Creates a CoreWeave AI Object Storage (CAIOS) bucket, including the
  prerequisite organization access policy if one does not already exist.
  Covers IAM role verification, access key creation, org policy setup,
  bucket creation (Console or CLI), and post-creation verification.
  Use this skill whenever a customer mentions creating a bucket, setting
  up object storage, provisioning S3 storage, storing training data or
  model checkpoints, or wants to use CoreWeave's S3-compatible storage —
  even if they don't say "create bucket" explicitly. Also use when someone
  asks about CAIOS, AI Object Storage, access keys, or organization
  access policies for storage on CoreWeave.
---

# Create a CoreWeave AI Object Storage bucket

You are helping a CoreWeave customer create an S3-compatible object storage bucket. This workflow covers the full chain of prerequisites: verifying IAM permissions, creating an access key, setting up an organization access policy (if one doesn't exist), and finally creating the bucket itself.

**The prerequisite chain matters.** A bucket cannot be created without an organization access policy, and policies cannot be managed without the Object Storage Admin IAM role. This skill checks each layer and either fixes it or gives the customer a clear explanation of what's missing and who can fix it.

**Do as much as possible before asking.** Probe the local environment silently — check kubeconfig, AWS CLI, existing credentials, and environment variables. Only ask the customer for information you cannot determine from local state.

---

## Before you start — silent environment probe

Run all of these checks silently before saying anything to the customer. Do not ask for permission to probe.

### 1. Check kubeconfig

```bash
kubectl config current-context 2>/dev/null
kubectl config get-contexts 2>/dev/null
```

If a CoreWeave context exists, extract the organization and zone:

```bash
kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}' 2>/dev/null
kubectl config view --minify -o jsonpath='{.users[0].user.token}' 2>/dev/null
```

Note the organization and cluster zone — this informs the default availability zone for the bucket (co-locate for best performance).

### 2. Check for AWS CLI

```bash
aws --version 2>/dev/null
```

If not installed, check for s3cmd as an alternative:

```bash
s3cmd --version 2>/dev/null
```

### 3. Check for existing credentials

Search for credentials in order of precedence:

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

### 4. Test existing access

If credentials were found, immediately test whether they work against the CoreWeave endpoint:

```bash
AWS_SHARED_CREDENTIALS_FILE=~/.coreweave/cw.credentials \
AWS_CONFIG_FILE=~/.coreweave/cw.config \
aws s3 ls --profile cw 2>&1
```

Or if credentials are in environment variables:

```bash
aws s3 ls --endpoint-url https://cwobject.com --region us-east-1 2>&1
```

### 5. Summarize findings to the customer

After all probes complete, present a brief summary of what you found and what's still needed. For example:

> "Here's what I found on your machine:
> - **Organization**: `acme-corp` (from kubeconfig context `use04a-dev`)
> - **AWS CLI**: installed (v2.x)
> - **Credentials**: found in `~/.coreweave/cw.credentials`
> - **Access test**: I can list your existing buckets — your credentials and org access policy are working.
>
> I just need a bucket name and zone, and I can create it right now."

Or if something is missing:

> "Here's what I found:
> - **Organization**: `acme-corp` (from kubeconfig)
> - **AWS CLI**: installed
> - **Credentials**: none found — you'll need an Object Storage access key.
>
> Let me walk you through creating one."

**The Cloud Console is at `console.coreweave.com`** (not `cloud.coreweave.com`).

---

## Step 1 — Ensure credentials exist

If the environment probe found working credentials (the `aws s3 ls` test succeeded), skip to Step 2.

If no credentials were found, check whether the customer already has an access key they haven't configured locally:

> "I didn't find any CoreWeave Object Storage credentials on this machine. Do you have an access key (Access Key ID + Secret Key) from a previous setup?"

### If they have a key pair

Configure it immediately via CLI:

```bash
mkdir -p ~/.coreweave

AWS_SHARED_CREDENTIALS_FILE=~/.coreweave/cw.credentials aws configure --profile cw
# Prompt: Access Key ID, Secret Key, region: us-east-1, output: json

AWS_CONFIG_FILE=~/.coreweave/cw.config aws configure set endpoint_url https://cwobject.com --profile cw
AWS_CONFIG_FILE=~/.coreweave/cw.config aws configure set s3.addressing_style virtual --profile cw
```

Then test access:

```bash
AWS_SHARED_CREDENTIALS_FILE=~/.coreweave/cw.credentials \
AWS_CONFIG_FILE=~/.coreweave/cw.config \
aws s3 ls --profile cw 2>&1
```

### If they don't have a key pair

They need to create one in the Console. Walk them through it concisely:

> "You'll need to create an access key:
> 1. Go to **console.coreweave.com** → **Object Storage** → **Access Keys** → **Create Key**
> 2. Name it (e.g., `dev-access`), select **Permanent Key**, click **Create**
> 3. **Copy both the Access Key ID and Secret Key now** — the secret is shown only once
> 4. Paste them back here and I'll configure everything."

Once they provide the key pair, run the `aws configure` commands above to set it up locally.

> **For production workloads**, recommend Workload Identity Federation instead of static keys. WIF exchanges short-lived OIDC or SAML tokens for temporary credentials, eliminating the need to store or rotate keys. See the [WIF documentation](https://docs.coreweave.com/products/storage/object-storage/auth-access/about) for setup.

---

## Step 2 — Verify S3 API access (tests org policy implicitly)

An organization access policy must exist for S3 API operations to work. Rather than asking the customer about policies, **test directly**:

```bash
AWS_SHARED_CREDENTIALS_FILE=~/.coreweave/cw.credentials \
AWS_CONFIG_FILE=~/.coreweave/cw.config \
aws s3 ls --profile cw 2>&1
```

### If the command succeeds

An org access policy is in place. Proceed to Step 3.

### If the command returns AccessDenied

The organization is missing an access policy. Explain what's needed and walk the customer through creating one:

> "Your credentials are valid, but your organization doesn't have an Object Storage access policy yet. This is a one-time setup that grants S3 API access. Let me walk you through it.
>
> Go to **console.coreweave.com** → **Policies** → **Object Storage access** tab → **Create policy**:
> 1. **Name**: `admin-full-access`
> 2. Add a statement: Name=`full-admin-access`, Access=Allow, Principals=`role/Admin`, Actions=`*`, Resources=`*`
> 3. Click **Submit**"

After they create it, re-test:

```bash
AWS_SHARED_CREDENTIALS_FILE=~/.coreweave/cw.credentials \
AWS_CONFIG_FILE=~/.coreweave/cw.config \
aws s3 ls --profile cw 2>&1
```

### If the customer can't create a policy (permission denied)

They need the **Object Storage Admin** IAM role. Explain:

> "Creating an organization access policy requires the **Object Storage Admin** IAM role. You'll need someone with the **IAM Admin** role on your organization to grant it to you at **console.coreweave.com** → **Policies** → **Platform access** tab."

**There is no read-only Object Storage role.** Object Storage Admin is the only IAM role for storage. For restricted access, use bucket-level policies after creation (see `references/bucket-policies.md`).

---

## Step 3 — Create the bucket

### Gather minimal configuration

By this point you already know the organization and likely the best zone (from kubeconfig). Collect only what you can't infer:

| Field | Required | Notes |
|-------|----------|-------|
| **Bucket name** | Yes | 3–63 characters. Lowercase letters, numbers, hyphens only. Must start and end with a letter or number. Must be **globally unique**. Cannot start with `xn--`, `cw-`, `vip-`, or `log-stitcher-ch-`. Cannot be named `int`. |
| **Availability zone** | Yes | Default to the zone from kubeconfig if available. Common zones: `US-EAST-04A`, `US-CENTRAL-05A`, `US-WEST-04A`. See `references/availability-zones.md` for the full list. |
| **Versioning** | No | Must be decided at creation time — **cannot be enabled later**. Default is off. Recommend for checkpoints or any data that benefits from rollback. |

Suggest a bucket name if the customer doesn't have one. Follow the pattern: `<org-or-project>-<purpose>-<zone-suffix>` (e.g., `acme-training-data-use04a`). Suggest the zone from the kubeconfig context as the default. Ask all questions at once — don't drip-feed them:

> "I'll create the bucket via CLI. I just need:
> - **Bucket name** — suggestion: `<suggested-name>`
> - **Zone** — I'd recommend `<zone>` to co-locate with your cluster. OK?
> - **Versioning** — off by default (can't be enabled later). Need it?"

### Create via AWS CLI (primary method)

```bash
AWS_SHARED_CREDENTIALS_FILE=~/.coreweave/cw.credentials \
AWS_CONFIG_FILE=~/.coreweave/cw.config \
aws s3api create-bucket \
    --bucket <BUCKET-NAME> \
    --region <AVAILABILITY-ZONE> \
    --create-bucket-configuration LocationConstraint=<AVAILABILITY-ZONE> \
    --profile cw
```

> **Note:** When creating a bucket via CLI, it takes about one minute before the bucket is available due to DNS caching.

### Create via S3cmd (alternative if aws CLI is unavailable)

```bash
s3cmd mb --bucket-location=<AVAILABILITY-ZONE> s3://<BUCKET-NAME>
```

### Create via Cloud Console (fallback if no CLI tools are installed)

If neither `aws` nor `s3cmd` is available and the customer can't install them:

**Navigation:** Cloud Console → **Object Storage** → **Buckets** → **Create Bucket**

1. Enter the bucket name.
2. Select the availability zone.
3. If versioning is needed, enable it before clicking Create.
4. Click **Create**.

---

## Step 4 — Verify the bucket

Verify via CLI immediately after creation:

```bash
AWS_SHARED_CREDENTIALS_FILE=~/.coreweave/cw.credentials \
AWS_CONFIG_FILE=~/.coreweave/cw.config \
aws s3 ls --profile cw
```

The new bucket should appear in the listing.

### Test upload

Run a quick upload test to confirm end-to-end access:

```bash
echo "test" > $TMPDIR/test-upload.txt
AWS_SHARED_CREDENTIALS_FILE=~/.coreweave/cw.credentials \
AWS_CONFIG_FILE=~/.coreweave/cw.config \
aws s3 cp $TMPDIR/test-upload.txt s3://<BUCKET-NAME>/test-upload.txt --profile cw
```

Then clean up:

```bash
AWS_SHARED_CREDENTIALS_FILE=~/.coreweave/cw.credentials \
AWS_CONFIG_FILE=~/.coreweave/cw.config \
aws s3 rm s3://<BUCKET-NAME>/test-upload.txt --profile cw
rm $TMPDIR/test-upload.txt
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

**Creating a bucket before setting up an organization access policy**
The bucket creation may succeed via the Console, but S3 API operations (uploads, downloads) will fail with access-denied errors until an org access policy is in place.

**Forgetting to save the secret key**
The secret key for an access key is shown only once at creation time. If lost, create a new key — the old one cannot be recovered.

**Choosing the wrong availability zone**
Place the bucket in the same zone as your compute workloads for best performance. Cross-zone access works but adds latency.

**Trying to enable versioning after creation**
Versioning cannot be enabled on an existing bucket. If versioning is needed, create a new bucket with versioning enabled and migrate data.

**Bucket name conflicts**
Bucket names are globally unique. If creation fails with a conflict, try a more specific name (include org name, project, or zone).

**Using `cw-` or `vip-` prefix**
These prefixes are reserved. Bucket names starting with them will be rejected.

**Confusing IAM roles with org access policies**
The Object Storage Admin IAM role grants control-plane access (managing buckets, keys, and policies). Organization access policies grant data-plane access (S3 API operations on objects). Both are required for full functionality.

---

## Next steps

After bucket creation, the customer may want to:

- **Copy files to/from the bucket** — use the `cw-use-bucket` skill.
- **Set a bucket access policy** for fine-grained control over who can access specific buckets. See `references/bucket-policies.md`.
- **Configure Workload Identity Federation** for production workloads. See the [WIF documentation](https://docs.coreweave.com/products/storage/object-storage/auth-access/about).
- **Set up LOTA** (Local Object Transport Accelerator) for workloads running inside CoreWeave. Use endpoint `http://cwlota.com` instead of `https://cwobject.com`.
- **Use Terraform** to manage buckets as infrastructure. See the [Terraform AWS provider guide](https://docs.coreweave.com/products/storage/object-storage/use-terraform-aws-provider).

---

## References

- `references/bucket-policies.md` — Per-bucket access policy examples and S3 action reference.
- `references/availability-zones.md` — Full list of supported availability zones for object storage.
- `references/browser-automation.md` — How to drive the Cloud Console with browser tools.
- [CoreWeave Object Storage docs](https://docs.coreweave.com/products/storage/object-storage)
- [Authentication and access control](https://docs.coreweave.com/products/storage/object-storage/auth-access)
- [Create a bucket](https://docs.coreweave.com/products/storage/object-storage/buckets/create-bucket)
- [Get started with CAIOS](https://docs.coreweave.com/products/storage/object-storage/get-started-caios)
