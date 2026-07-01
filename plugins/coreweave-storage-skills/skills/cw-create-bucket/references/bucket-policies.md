# Bucket-level access policies

Bucket policies provide fine-grained control over who can access a specific bucket and what actions they can perform. They are **optional** — the organization access policy alone is sufficient for most use cases. Use bucket policies when you need to restrict or delegate access per-bucket.

---

## When to use bucket policies

- Grant a contractor read-only access to a specific bucket without giving them org-wide permissions.
- Restrict a team to only their project's bucket.
- Allow a service account to write checkpoints but not delete objects.

## Org access policy vs. bucket policy

| Layer | Scope | Required? | Controls |
|-------|-------|-----------|----------|
| **Organization access policy** | All buckets in the org | Yes (at least one) | Baseline S3 API access |
| **Bucket policy** | One specific bucket | No | Per-bucket action-level restrictions |

Both layers are evaluated. Access is granted only if both the org policy and bucket policy (if one exists) allow the action. A bucket with no bucket policy inherits the org policy as-is.

## Common S3 actions

| Action | Description |
|--------|-------------|
| `s3:GetObject` | Download objects |
| `s3:PutObject` | Upload objects |
| `s3:DeleteObject` | Delete objects |
| `s3:ListBucket` | List objects in a bucket |
| `s3:GetBucketPolicy` | Read the bucket policy |
| `s3:PutBucketPolicy` | Set or update the bucket policy |
| `s3:CreateBucket` | Create buckets |
| `s3:DeleteBucket` | Delete buckets |
| `s3:*` | All S3 actions |

## Example: read-only access to a specific bucket

This bucket policy allows a specific access key to only read and list objects:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {"AWS": ["arn:aws:iam:::user/<ACCESS-KEY-ID>"]},
      "Action": ["s3:GetObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::<BUCKET-NAME>",
        "arn:aws:s3:::<BUCKET-NAME>/*"
      ]
    }
  ]
}
```

## Setting a bucket policy

**Via Console:** Navigate to **Object Storage** → **Buckets** → select the bucket → **Permissions** → **Edit policy**.

**Via AWS CLI:**

```bash
AWS_SHARED_CREDENTIALS_FILE=~/.coreweave/cw.credentials \
AWS_CONFIG_FILE=~/.coreweave/cw.config \
aws s3api put-bucket-policy \
    --bucket <BUCKET-NAME> \
    --policy file://policy.json \
    --profile cw
```

## Further reading

- https://docs.coreweave.com/products/storage/object-storage/auth-access
