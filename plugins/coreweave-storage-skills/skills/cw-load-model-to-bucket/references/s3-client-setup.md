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

## Endpoints

| Endpoint | When to use | Notes |
|----------|-------------|-------|
| `https://cwobject.com` | Outside a CoreWeave cluster (laptops, CI, other clouds) | Primary endpoint. Requires **TLS v1.3** — ensure the client and its OpenSSL support it. |
| `http://cwlota.com` | Inside a CoreWeave cluster (CKS/SUNK nodes) | The [LOTA](https://docs.coreweave.com/products/storage/object-storage/improving-performance/about-lota) endpoint; routes through the node-local cache for best throughput. |

---

## Install the tools

- **AWS CLI v2** — https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html
- **jq** — `brew install jq` / `apt-get install jq`
- **Hugging Face CLI** — `pip install -U "huggingface_hub[cli]"` (provides `huggingface-cli`)
- **s3cmd** (optional) — `pip install s3cmd` / `brew install s3cmd`
- **s5cmd — CoreWeave fork (optional, recommended for large models)**. Do **not**
  use upstream `s5cmd`: it uses path-style addressing and is incompatible with
  CAIOS. Download the release binary from
  https://github.com/coreweave/s5cmd/releases, then:
  ```bash
  chmod +x s5cmd && sudo mv s5cmd /usr/local/bin/
  s5cmd version
  ```
  The fork defaults to virtual-hosted addressing for CAIOS and safely replaces
  any existing `s5cmd` install (other S3 backends are unaffected).

---

## Configure the AWS CLI

### Option A — dedicated `cw` profile in the standard AWS files (used by the workflow)

Add a profile to `~/.aws/config` with the endpoint and virtual addressing baked
in, and the matching credentials to `~/.aws/credentials`:

```ini
# ~/.aws/config
[profile cw]
region = US-EAST-04A
endpoint_url = https://cwobject.com
s3 =
    addressing_style = virtual
```

```ini
# ~/.aws/credentials
[cw]
aws_access_key_id = <ACCESS-KEY-ID>
aws_secret_access_key = <SECRET-ACCESS-KEY>
```

Then every command takes `--profile cw` (or `export AWS_PROFILE=cw`). Inline
`endpoint_url` in a profile requires a recent AWS CLI v2.

### Option B — isolated CoreWeave config files

Keep CoreWeave settings entirely out of your default AWS files by pointing the
CLI at a separate config directory:

```bash
AWS_SHARED_CREDENTIALS_FILE=~/.coreweave/cw.credentials aws configure --profile cw
AWS_CONFIG_FILE=~/.coreweave/cw.config aws configure set endpoint_url https://cwobject.com --profile cw
AWS_CONFIG_FILE=~/.coreweave/cw.config aws configure set default.s3.addressing_style virtual --profile cw
```

With Option B you must export both env vars (`AWS_SHARED_CREDENTIALS_FILE` and
`AWS_CONFIG_FILE`) in any shell that runs `aws --profile cw`.

---

## Configure s3cmd

Run the interactive configurator and provide CAIOS values:

```bash
s3cmd --configure
```

| Prompt | Value |
|--------|-------|
| Access Key | Your CAIOS access key ID |
| Secret Key | Your CAIOS secret key |
| Default Region | A CoreWeave Availability Zone (see below), e.g. `US-EAST-04A` |
| S3 Endpoint | `cwobject.com` (or `cwlota.com` inside a cluster) |
| DNS-style bucket+hostname template | `%(bucket)s.cwobject.com` (or `%(bucket)s.cwlota.com`) |
| Use HTTPS protocol | `True` for the primary endpoint |

Leave the rest at defaults. Config is saved to `~/.s3cfg`.

Common s3cmd commands used by the workflow:

```bash
s3cmd mb --bucket-location=<AZ> s3://<BUCKET-NAME>   # create a bucket
s3cmd put <LOCAL-FILE> s3://<BUCKET-NAME>            # upload an object
s3cmd ls s3://<BUCKET-NAME>/                         # list objects
```

---

## Configure s5cmd (CoreWeave fork)

`s5cmd` reads credentials from the standard AWS chain, so it reuses the `cw`
profile or plain environment variables. Pass the endpoint explicitly; the fork
supplies virtual-hosted addressing:

```bash
# Via the cw profile:
AWS_PROFILE=cw s5cmd --endpoint-url https://cwobject.com ls s3://<BUCKET-NAME>/

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
