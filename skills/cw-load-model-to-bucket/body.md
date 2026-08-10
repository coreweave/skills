
# Provision a CAIOS bucket and load a Hugging Face model

You are helping a CoreWeave customer stand up a **CoreWeave AI Object Storage
(CAIOS)** bucket and populate it with a model's weights pulled from Hugging
Face. By the end, the customer has a bucket in a CAIOS-supported Availability
Zone containing a complete model directory (config, tokenizer, and
`safetensors` weights) at a known path — the exact artifact a **CoreWeave
Inference Bring-Your-Own-Weights (BYOW)** deployment consumes.

The default model is **`google/gemma-3-270m-it`**. At about 576 MB, it is quick
to move end-to-end so the customer can validate the whole pipeline before
swapping in a larger model. Hugging Face gates access until the customer accepts
the Gemma terms and authenticates. Any Hugging Face model ID works. Substitute
the customer's choice wherever the default appears.

---

## What this skill does — and what it doesn't

CoreWeave Inference uses a **Bring-Your-Own-Weights** model: it **does not pull
weights directly from Hugging Face**. You download the weights yourself and
upload them to a CAIOS bucket, then point a deployment at
`bucket-name` + `path/to/model`. This skill is exactly that preparation step.

- ✅ **This skill:** creates a CAIOS bucket and stages model weights into it.
- ❌ **Not this skill:** it does **not** deploy a serving endpoint. If the
  customer wants a running vLLM server that downloads from Hugging Face at
  runtime on a CKS cluster, use **`cw-self-managed-inference`** instead. If
  they want a managed BYOW deployment, this skill produces its input, and the
  hand-off in Step 9 points to the deployment docs.

Set that expectation up front so the customer knows what they'll have at the end.

> **If the customer asked for a bucket AND a self-managed endpoint, settle it
> before you build either.** That request sounds like one pipeline and is really
> two alternatives: `cw-self-managed-inference` downloads its model from Hugging
> Face at runtime and never reads a CAIOS bucket. Tell them so, then let them
> choose — keep the bucket as a ready-made artifact for a future managed BYOW
> deployment, or skip it and go straight to the endpoint. Staging weights the
> endpoint will not use is fine as long as the customer knows that is what they
> are getting. It is only a problem when nobody says so.

---

## Before you start

Confirm the following:

- The customer has a **CoreWeave organization** and can sign in to the Cloud
  Console at **`console.coreweave.com`**.
- Their user can create Object Storage credentials and buckets. Creating an
  access key requires the **`Object Storage Admin`** IAM role (or an
  organization access policy granting `cwobject:CreateAccessKey`); creating a
  bucket additionally requires **`s3:CreateBucket`**. If they hit a `403`
  later, this is almost always the cause — have an org admin grant the role
  (see `cw-add-users`).
- They have a **workstation with enough free disk and network** to hold the
  model twice over (once downloaded, once in flight). Gemma 3 270M needs about
  1.5 GB free. A 70B model needs hundreds of GB.
- **Command-line tools** are installed. Verify quickly and guide installation
  for anything missing:
  ```bash
  aws --version              # AWS CLI v2 (S3-compatible client)
  jq --version               # JSON parsing for the access-key response
  python3 --version          # needed by the Hugging Face CLI
  hf version                 # Hugging Face download client (NOT huggingface-cli, which is deprecated)
  cwic auth whoami           # optional: CoreWeave CLI — if signed in, it mints the S3 key in one command (Step 1)
  s5cmd version              # optional, for fast bulk uploads of large models
  ```
  If the AWS CLI, `jq`, or `hf` are missing, see
  `references/s3-client-setup.md` for install pointers (it also covers `s3cmd`
  and the **CoreWeave fork of `s5cmd`**, which you must use instead of upstream
  `s5cmd`).
- Do all local work in the scratch directory `/tmp/claude/models` so nothing
  lands in the customer's project tree unless they ask.
- **Assume the workstation already has S3 client configuration you must not
  disturb.** Real machines carry `~/.aws` profiles for other AWS accounts,
  other S3-compatible providers, and sometimes a second CoreWeave org. This
  workflow therefore never edits `~/.aws` — Step 3 uses an isolated per-run
  config instead. Don't "helpfully" consolidate or rewrite the customer's
  existing profiles.

This is a **create-and-write** workflow: it provisions a bucket and uploads
data. There are cost implications (stored data is billed) — call them out
before creating anything, and confirm the model choice and bucket name with the
customer at the checkpoints below.

---

## Step 1 — Get CAIOS credentials the shortest way available

CAIOS speaks the S3 API, which needs an **access key ID + secret key**. There
are two supported ways to mint one — check for the short way first.

### Path A — `cwic` (preferred when it's installed and signed in)

The CoreWeave CLI mints an Object Storage access key in one authenticated
command — no Console visit, no API token, no curl. Probe for it first:

```bash
command -v cwic >/dev/null && cwic auth whoami
```

If that prints `Currently authenticated as: <org>`, confirm with the customer
that this is the org they want the bucket in (`cwic auth switch` changes
accounts on machines with several), then mint the key. `--duration` takes a
lifetime in seconds (max `43200` = 12 hours) or the word `Permanent`; a
temporary key is more secure if the upload will finish inside its lifetime:

```bash
out="$(cwic cwobject token create --name model-bucket-key --duration 43200)"
printf '%s\n' "$out" | grep -v 'Secret Key'
export AWS_ACCESS_KEY_ID="$(printf '%s\n' "$out" | awk '/Access Key ID:/{print $NF}')"
export AWS_SECRET_ACCESS_KEY="$(printf '%s\n' "$out" | awk '/Secret Key:/{print $NF}')"
[ -n "$AWS_ACCESS_KEY_ID" ] && echo "access key captured: $AWS_ACCESS_KEY_ID" \
  || echo "could not parse the cwic output — inspect it and capture the two values manually"
```

The output's `Access Key ID:` / `Secret Key:` lines are the credential — the
secret is shown only once, so treat the captured variables as sensitive.
**With the key exported, skip Step 2 entirely and go to Step 3.**

### Path B — Console token + exchange (when `cwic` is missing or signed out)

Everything downstream authenticates with a CoreWeave API access token. Create
one now; in Step 2 you exchange it for an Object Storage access key.

{{include:create-api-token}}

After this step you should have the token exported as `CW_API_TOKEN` in your
shell. Confirm it's set before continuing:

```bash
[ -n "$CW_API_TOKEN" ] && echo "token is set" || echo "CW_API_TOKEN is empty — set it before continuing"
```

---

## Step 2 — Exchange the token for an Object Storage access key

**Skip this step if Step 1's `cwic` path already exported
`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`.**

The S3 API needs an **access key ID + secret key**, not the raw API token.
The recommended path is to exchange your API token directly for
credentials — no separate static key to manage in the Console.

Create a small request body and call the access-key endpoint. `durationSeconds`
controls the key's lifetime: **`0` creates a permanent key**; a positive integer
creates a temporary key valid for that many seconds (max `43200` = 12 hours). A
permanent key is simplest for a one-off model upload — you can revoke it
afterward — but a temporary key is more secure if the upload will finish inside
its lifetime.

```bash
mkdir -p /tmp/claude/models

cat > /tmp/claude/models/keyreq.json <<'EOF'
{
  "durationSeconds": 0,
  "attributes": { "name": "model-bucket-key" }
}
EOF

curl -sS -X POST https://api.coreweave.com/v1/cwobject/access-key \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $CW_API_TOKEN" \
  -d @/tmp/claude/models/keyreq.json \
  | tee /tmp/claude/models/keyresp.json | jq .
```

The live API response uses the exact fields `accessKeyId` and `secretKey`.
Capture them into the standard AWS environment variables:

```bash
export AWS_ACCESS_KEY_ID=$(jq -r '.accessKeyId' /tmp/claude/models/keyresp.json)
export AWS_SECRET_ACCESS_KEY=$(jq -r '.secretKey' /tmp/claude/models/keyresp.json)

[ -n "$AWS_ACCESS_KEY_ID" ] && [ "$AWS_ACCESS_KEY_ID" != "null" ] \
  && echo "access key captured: $AWS_ACCESS_KEY_ID" \
  || echo "no key in response — check keyresp.json (usually a 403: missing Object Storage Admin role)"
```

> **Secret handling:** the secret key is shown only in this response. Treat
> `keyresp.json` as sensitive — store the secret in your password manager and
> delete the file when done (`rm /tmp/claude/models/keyresp.json`). Do not paste
> it into shared logs or values files.

**Alternative — Cloud Console:** if the customer prefers a UI or lacks
`cwobject:CreateAccessKey` for the exchange, they can create a static key from
the **Access Keys** page at `console.coreweave.com/object-storage/access-keys`
(**Create Key** → name it → permanent or temporary → **Create**) and copy the
Access Key ID and Secret Key. For production workloads, prefer **Workload
Identity Federation** over static keys — see the references.

---

## Step 3 — Configure your S3 client for CAIOS

CAIOS requires two non-default settings on any S3 client:

- **Endpoint** `https://cwobject.com` (the primary endpoint, for use **outside**
  a CoreWeave cluster; it requires TLS v1.3). Inside a CoreWeave cluster, use
  the LOTA endpoint `http://cwlota.com` instead for best performance.
- **Virtual-hosted addressing style** (`addressing_style = virtual`). CAIOS does
  **not** support path-style addressing; this is the single most common
  misconfiguration.

**Never append to or rewrite `~/.aws/config` / `~/.aws/credentials`.** Real
workstations already carry profiles for other AWS accounts, other
S3-compatible providers, and sometimes a second CoreWeave org — and a pasted
config block silently hijacks whatever profile name it collides with, while
re-running it appends duplicate sections in which the last definition of each
key quietly wins. You may not even be able to *read* `~/.aws` to check (some
agent sandboxes deny it); that is a reason to stay out of the file, not to
append blind.

Instead, write a **fully isolated, per-run config** in the scratch directory.
It can't collide with anything, works even when `~/.aws` is unreadable, and is
cleaned up with the scratch directory:

```bash
mkdir -p /tmp/claude/models
export AWS_CONFIG_FILE=/tmp/claude/models/aws-config
export AWS_SHARED_CREDENTIALS_FILE=/tmp/claude/models/aws-credentials

aws configure set profile.cw.endpoint_url https://cwobject.com
aws configure set profile.cw.s3.addressing_style virtual
aws configure set aws_access_key_id "$AWS_ACCESS_KEY_ID" --profile cw
aws configure set aws_secret_access_key "$AWS_SECRET_ACCESS_KEY" --profile cw
```

(The profile's `region` is set in Step 5, once the Availability Zone is
confirmed — don't guess it now.)

From here on, **every `aws` command needs both `AWS_*_FILE` variables exported
plus `--profile cw`**. Shells often don't persist between commands in agent
environments — re-export both variables in each new shell (or prefix them onto
each command). If they're missing, the CLI silently falls back to `~/.aws` and
the customer's default profile, which is exactly the cross-contamination this
setup avoids.

**If the customer explicitly asks for a persistent profile** in their real
`~/.aws` instead: ask before touching the file, list what already exists with
`aws configure list-profiles`, and pick a name that is **not taken** — don't
assume `cw` is free; on multi-org machines it often belongs to another
CoreWeave account. Then write it only with
`aws configure set profile.<name>.<key> <value>` (which rewrites files
safely), never with appended heredoc blocks.

If the customer uses `s3cmd` or `s5cmd` instead of the AWS CLI, follow
`references/s3-client-setup.md` — the endpoint and virtual-addressing
requirements are the same.

---

## Step 4 — Choose the model

Confirm the model with the customer. Default to the small starter unless they
name a specific model:

| Hugging Face model ID | Size | Gated? | Good for |
|-----------------------|------|--------|----------|
| `google/gemma-3-270m-it` | ~576 MB | **Yes** | Default, fastest end-to-end validation |
| `TinyLlama/TinyLlama-1.1B-Chat-v1.0` | ~2.2 GB | **No** | Ungated alternative that needs no token |
| `Qwen/Qwen2.5-7B-Instruct` | ~15 GB | **No** | Higher quality, still no token |
| `meta-llama/Llama-3.1-8B-Instruct` | ~16 GB | **Yes** | Popular, requires accepting the license + a HF token |
| `mistralai/Mistral-7B-Instruct-v0.3` | ~15 GB | **Yes** | Requires license acceptance + a HF token |

Record the choice as `HF_MODEL` and a short, S3-safe folder name for it:

```bash
export HF_MODEL="google/gemma-3-270m-it"
export MODEL_DIR="gemma-3-270m-it"   # object-key prefix inside the bucket
```

The default Gemma model is gated. Before Step 6, the customer must accept the
[Gemma terms](https://ai.google.dev/gemma/terms) on the model's Hugging Face
page and have a Hugging Face token with read access. Other gated models require
the same license-acceptance and authentication flow.

---

## Step 5 — Provision the bucket

Pick an **Availability Zone that supports CAIOS** and a **globally unique bucket
name**. Bucket names have strict rules — the two that trip people up: names must
be **lowercase letters, digits, and hyphens only**, and they **must not begin
with `cw-`** (that prefix and `vip-`, `log-stitcher-ch-`, and the exact name
`int` are reserved by CoreWeave). Full rules and the AZ list are in
`references/s3-client-setup.md`.

```bash
export CW_AZ="US-EAST-04A"              # an AZ that supports CAIOS
export CW_BUCKET="acme-model-weights"   # globally unique; NOT starting with cw-

# Now that the AZ is confirmed, set it as the profile's region (same isolated
# config files from Step 3 — make sure both AWS_*_FILE variables are exported
# in this shell too):
aws configure set profile.cw.region "$CW_AZ"
```

> **Checkpoint:** show the customer the bucket name and AZ and get a thumbs-up
> before creating it. The name must be globally unique across all CAIOS
> customers, so have a fallback ready if it's taken.

Create the bucket. The `LocationConstraint` is required and must match the AZ:

```bash
aws s3api create-bucket \
  --bucket "$CW_BUCKET" \
  --region "$CW_AZ" \
  --create-bucket-configuration LocationConstraint="$CW_AZ" \
  --profile cw
```

> **Expect a ~1-minute delay.** After creation via an S3 client, the bucket
> takes about a minute to become usable due to DNS caching. Commands run
> immediately may fail with
> `An error occurred (InvalidRegion) ... Region does not match.` — wait a minute
> and retry. (Buckets created in the Cloud Console don't have this delay.)

Verify the bucket is reachable (retry once if you hit the region error):

```bash
aws s3 ls "s3://$CW_BUCKET/" --profile cw && echo "bucket is ready"
```

---

## Step 6 — Download the model from Hugging Face

Install the Hugging Face CLI if needed (the `[cli]` extra no longer exists,
and `pip` on a system/Homebrew Python may require a scratch virtualenv — see
`references/s3-client-setup.md`):

```bash
pip install -U huggingface_hub
```

For a gated model, including the default Gemma model, confirm that the customer
accepted the model terms on Hugging Face. Then authenticate with a Hugging Face
token that has read access:

```bash
export HF_TOKEN="<your-huggingface-token>"
hf auth login --token "$HF_TOKEN"
```

Download the full model directory into the scratch workspace:

```bash
hf download "$HF_MODEL" \
  --local-dir "/tmp/claude/models/$MODEL_DIR"
```

This pulls every file in the repo, including `config.json`, tokenizer files,
and the `*.safetensors` weights.

Confirm the download landed:

```bash
ls -lh "/tmp/claude/models/$MODEL_DIR"
```

You should see `config.json`, one or more `*.safetensors` files, and tokenizer
files. Newer `hf` versions also create a `.cache/` subdirectory
here — that's local metadata, and Step 7 excludes it from the upload.

---

## Step 7 — Upload the weights to the bucket

Upload the model directory under the `$MODEL_DIR` prefix, excluding the local
`.cache/` metadata folder. The AWS CLI handles this reliably for models of any
size that fit the earlier disk check:

```bash
aws s3 cp "/tmp/claude/models/$MODEL_DIR/" "s3://$CW_BUCKET/$MODEL_DIR/" \
  --recursive \
  --exclude ".cache/*" \
  --profile cw
```

**For large models (tens of GB or more), use the CoreWeave `s5cmd` fork** — it's
markedly faster for bulk transfers. It reads the same isolated config files
(the AWS SDK honors both variables); the fork defaults to virtual-hosted
addressing, so you only pass the endpoint:

```bash
AWS_PROFILE=cw \
AWS_CONFIG_FILE=/tmp/claude/models/aws-config \
AWS_SHARED_CREDENTIALS_FILE=/tmp/claude/models/aws-credentials \
s5cmd --endpoint-url https://cwobject.com \
  cp --exclude ".cache/*" \
  "/tmp/claude/models/$MODEL_DIR/" \
  "s3://$CW_BUCKET/$MODEL_DIR/"
```

> If you're running this **inside** a CoreWeave cluster, swap the endpoint for
> the LOTA endpoint `http://cwlota.com` for best throughput.

---

## Step 8 — Verify the upload

Confirm the weights are actually in the bucket and the key files are present:

```bash
aws s3 ls "s3://$CW_BUCKET/$MODEL_DIR/" --recursive --human-readable --profile cw
```

**Pass criteria:**

- `config.json` is present.
- At least one `*.safetensors` (or `*.bin`) weights file is present, and its
  size matches the local download (not 0 bytes).
- Tokenizer files (`tokenizer.json` / `tokenizer.model` / `tokenizer_config.json`)
  are present.

Spot-check that the object count and total size line up with the local
directory (minus the excluded `.cache/`). If anything is missing, re-run the
upload for the missing files — S3 copies are idempotent, so re-running is safe.

Record the final location — this is what a deployment will reference:

```
Bucket:      $CW_BUCKET   (in $CW_AZ)
Model path:  $MODEL_DIR/
S3 URI:      s3://$CW_BUCKET/$MODEL_DIR/
```

---

## Step 9 — Report and hand off

Summarize for the customer:

- **What exists now:** bucket `$CW_BUCKET` in `$CW_AZ`, containing the
  `$HF_MODEL` weights at `s3://$CW_BUCKET/$MODEL_DIR/`.
- **Using it for BYOW inference:** when creating a CoreWeave Inference
  deployment, provide the **bucket name** (`$CW_BUCKET`) and the **path to the
  model directory** (`$MODEL_DIR/`). CoreWeave loads the weights onto GPU
  infrastructure and serves them — see the BYOW docs in the references.
- **Or self-managed serving:** if they'd rather run their own vLLM server on
  CKS (which downloads from Hugging Face at runtime rather than from this
  bucket), hand off to **`cw-self-managed-inference`**.
- **Cost & cleanup:** stored data is billed while it sits in the bucket. To tear
  it down later, empty and delete the bucket (see the references). If you created
  a **permanent** access key (either path in Step 1/2) and no longer need it,
  revoke it from the Console Access Keys page. Delete
  `/tmp/claude/models/keyresp.json` if you haven't already.

Then clean up the local download if the customer doesn't need it. The isolated
client config from Step 3 (`aws-config` / `aws-credentials`, which holds the
secret key) lives in the same scratch tree — remove it too once the upload is
verified:

```bash
rm -rf "/tmp/claude/models/$MODEL_DIR" \
       /tmp/claude/models/aws-config /tmp/claude/models/aws-credentials
```

The customer's own `~/.aws` was never touched, so there is nothing to restore.

---

## Troubleshooting

**`403 Forbidden` / `AccessDenied` creating the key or bucket**
The user is missing permissions. Creating a key needs the **Object Storage
Admin** role (or `cwobject:CreateAccessKey`); creating a bucket needs
`s3:CreateBucket`. Ask an org admin to grant the role, then re-run.

**`InvalidRegion` / `Region does not match` right after creating the bucket**
Expected for about a minute after S3-client creation, due to DNS caching. Wait
and retry. Persisting well past a minute usually means the `region` /
`LocationConstraint` doesn't match a CAIOS-supported AZ — confirm against the AZ
list in the references.

**`The config profile (cw) could not be found`**
The `AWS_CONFIG_FILE` / `AWS_SHARED_CREDENTIALS_FILE` variables aren't
exported in the current shell, so the CLI is looking in `~/.aws`. Re-export
both (Step 3) — don't "fix" it by writing the profile into `~/.aws`.

**`SignatureDoesNotMatch`, hangs, or 400s on every call**
Almost always path-style addressing. CAIOS requires **virtual-hosted** style —
confirm `addressing_style = virtual` is set on the `cw` profile (Step 3) and,
for `s5cmd`, that you're using the **CoreWeave fork**, not upstream.

**The customer's other AWS/S3 tooling broke after an earlier run**
A previous run probably appended a profile block to `~/.aws/config` or
`~/.aws/credentials` (older versions of this workflow did). Look for
duplicated section headers — with duplicates, the last definition of each key
wins, so an appended `[profile X]` hijacks the original. Show the customer the
duplicated sections and let them decide which block to keep; removing the
appended block restores the original behavior. This workflow's isolated config
(Step 3) can't cause this.

**`BucketAlreadyExists` / name taken**
Bucket names are globally unique across all CAIOS customers. Pick another name
(and remember it can't start with `cw-`, `vip-`, `log-stitcher-ch-`, or be `int`).

**`401` / gated-model download fails**
The model requires accepting its license on Hugging Face and a token. Accept the
license on the model page, then `hf auth login` with a token that has
read access (Step 6). The default Gemma model requires both steps.

**TLS errors against `https://cwobject.com`**
The primary endpoint requires **TLS v1.3**. Ensure the client and its OpenSSL
support it, or (only inside a CoreWeave cluster) use the LOTA endpoint.

---

## References

- `references/s3-client-setup.md` — Installing and configuring the AWS CLI,
  `s3cmd`, and the CoreWeave `s5cmd` fork for CAIOS; endpoints; the full bucket
  naming rules; and the list of Availability Zones that support CAIOS.
- CoreWeave docs — Get started with AI Object Storage:
  https://docs.coreweave.com/products/storage/object-storage/get-started-caios
- CoreWeave docs — Create a bucket:
  https://docs.coreweave.com/products/storage/object-storage/buckets/create-bucket
- CoreWeave docs — Manage access keys (direct token exchange):
  https://docs.coreweave.com/products/storage/object-storage/auth-access/manage-access-keys/about
- CoreWeave docs — Migrate data with s5cmd:
  https://docs.coreweave.com/products/storage/object-storage/migrate-data
- CoreWeave docs — Bring Your Own Weights (Inference):
  https://docs.coreweave.com/products/inference/models
- CoreWeave docs — Empty and delete a bucket:
  https://docs.coreweave.com/products/storage/object-storage/buckets/empty-and-delete-bucket
- Hugging Face: google/gemma-3-270m-it:
  https://huggingface.co/google/gemma-3-270m-it
- Google AI for Developers: Gemma terms:
  https://ai.google.dev/gemma/terms
