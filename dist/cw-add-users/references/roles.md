# CoreWeave IAM Roles — Plain-English Guide

Use this reference when a user asks "what does this role do?" or "which roles should I give my team?"

---

## Access Token Viewer
**What it lets you do:** See the list of personal API access tokens for your own account. Read-only.

**Who typically gets this:** Anyone who needs visibility into their own tokens but shouldn't manage them programmatically. Often paired with other "Viewer" roles for a read-only bundle.

---

## Access Token Admin
**What it lets you do:** Create and delete personal API access tokens for your own account. Needed for anyone who uses the CoreWeave API, CLI, or Terraform.

**Who typically gets this:** Developers, DevOps engineers, anyone who needs programmatic access.

---

## IAM Viewer
**What it lets you do:** Read-only view of IAM configuration — users, groups, policies, SAML settings, API token list. Can't change anything.

**Who typically gets this:** Team leads or compliance roles who need visibility without edit access.

---

## IAM Admin
**What it lets you do:** Full control over identity and access management — invite/remove users, create/edit/delete groups and policies, configure SAML SSO, manage API tokens across the org.

**Who typically gets this:** Actual organization administrators only. A user with IAM Admin can invite anyone and assign any permission, including to themselves.

⚠️ Be conservative. Most engineers and team leads do not need IAM Admin.

---

## CKS Viewer
**What it lets you do:** Read-only access to Kubernetes (CKS) resources — list and view clusters and VPC resources. Can't create, modify, or delete anything.

**Who typically gets this:** Contractors, auditors, stakeholders who need visibility into Kubernetes infrastructure but shouldn't touch it.

---

## CKS Admin
**What it lets you do:** Full control over Kubernetes resources — create, update, delete clusters and VPC resources.

**Who typically gets this:** DevOps engineers, platform engineers, ML engineers who need to provision and manage Kubernetes clusters.

---

## Object Storage Admin
**What it lets you do:** Full administration of AI Object Storage (S3-compatible) at the control-plane level — create/delete buckets, manage organization access policies, create/revoke access keys.

**Who typically gets this:** Developers and engineers who need to store and retrieve data.

⚠️ **There is no "Object Storage Viewer" role.** CoreWeave IAM only has the admin role for object storage. If you need read-only storage access (e.g., for contractors debugging), you have two options:
1. **Grant Object Storage Admin** and lock access down at the **bucket-level policy** (a separate system that controls individual S3 actions like `s3:Get*`, `s3:List*`). This keeps the IAM control-plane role but restricts what they can actually do on specific buckets.
2. **Omit object storage from IAM roles entirely** and share specific data with them out-of-band (pre-signed URLs, separate access keys with scoped policies).

See https://docs.coreweave.com/products/storage/object-storage/auth-access for bucket-level policy details.

---

## Billing Viewer
**What it lets you do:** Read-only access to billing — view the billing dashboard, current balance, invoices.

**Who typically gets this:** Finance team, team leads, anyone who needs to monitor costs. Most engineers don't need this.

---

## Observability Viewer
**What it lets you do:** Read-only access to cluster metrics, dashboards, and performance monitoring data.

**Who typically gets this:** Engineers and ops teams who need to monitor workload performance and troubleshoot issues.

---

## Notifications Viewer
**What it lets you do:** Read-only access to alert history and notification delivery status.

**Who typically gets this:** On-call engineers, SREs. Usually paired with Observability Viewer.

---

## Notifications Admin
**What it lets you do:** Create, update, and delete notification destinations (Slack webhooks, etc.) and alert integrations. Includes Notifications Viewer permissions.

**Who typically gets this:** Whoever owns alerting/incident management. Usually one or two people.

---

## Support Viewer
**What it lets you do:** Read-only access to support tickets in the integrated Freshdesk support system.

**Who typically gets this:** Team leads, managers who want visibility into open support issues.

---

## Access Request Approver
**What it lets you do:** Approve or deny privileged access requests for Service Account Management.

**Who typically gets this:** Administrators and team leads responsible for approving elevated access.

---

## Quick reference: role bundles

| Use case | Recommended roles |
|---|---|
| Standard engineer | CKS Admin, Object Storage Admin, Access Token Admin, Observability Viewer |
| Read-only / contractor | CKS Viewer, IAM Viewer, Access Token Viewer, Observability Viewer (see Object Storage note above) |
| Team lead + billing | Standard engineer roles + Billing Viewer, Notifications Viewer |
| On-call / SRE | Standard engineer roles + Notifications Admin |
| Full admin (rare) | IAM Admin, CKS Admin, Object Storage Admin, Access Token Admin, Access Request Approver |

---

## Legacy group equivalents (if migrating)

| Old group | Equivalent roles |
|---|---|
| admin | IAM Admin, CKS Admin, Object Storage Admin, Access Token Admin, Access Request Approver |
| write | CKS Admin, Object Storage Admin, Access Token Admin |
| read | IAM Viewer, CKS Viewer, Access Token Viewer |
| metrics | Observability Viewer |
| billing_viewer | Billing Viewer |
