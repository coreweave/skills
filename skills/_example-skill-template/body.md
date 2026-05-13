<!--
  EXAMPLE BODY — not production content.

  This file is a TEMPLATE. Do not ship it as a real skill. Copy the
  directory to skills/<your-skill>/ and replace the prose below with
  bespoke content describing your workflow.

  How body.md works:
    - The build prepends the frontmatter from skill.yaml.
    - Everywhere you write `{{include:SNIPPET_NAME}}` (on its own line),
      the build replaces the marker with the resolved body of that
      snippet from _snippets/, with parameter values from skill.yaml
      already substituted in.
    - Everything else is copied through verbatim. Write prose, code
      blocks, headings — whatever the workflow needs.
    - The include syntax is single-curly-braces around `include:` so it
      does NOT collide with Jinja2's double-brace syntax used inside
      snippets for parameters.
-->

# Example workflow: do the thing

> **NOTE — this is a template skill.** Replace this notice and the prose
> below before submitting. Real skills should open with a one-paragraph
> summary of what the workflow accomplishes for the customer and the
> end-state the customer can expect.

This skill walks a CoreWeave customer through an example workflow that:

1. Provisions credentials.
2. Wires up cluster access.
3. Verifies the end result is healthy.

## Prerequisites

- A CoreWeave organization with at least one CKS cluster already created.
- IAM Admin role on that organization (needed for the token step below).
- `kubectl` and the `coreweave` CLI installed locally.

## Step 1 — Get an API token

The first thing we need is a scoped API token. The block below is
inlined from a shared snippet so every workflow stays consistent on
exactly *how* tokens are created.

{{include:create-api-token}}

## Step 2 — Point kubectl at the cluster

With the token in hand, generate a kubeconfig and merge it in:

{{include:generate-kubeconfig}}

## Step 3 — Do the workflow-specific work

This section is the **bespoke** part — the reason this skill exists at
all. Snippets handle the boilerplate; everything that's unique to *this*
workflow goes here as ordinary prose, code blocks, and decision trees.

For the example template we'll just `kubectl get pods -A` and pretend
that constitutes a workflow:

```bash
kubectl get pods -A
```

You should see system pods in `Running` state. If anything is in
`CrashLoopBackOff`, stop and capture the pod logs before continuing.

## Step 4 — Verify in Grafana

Don't trust the CLI alone — Grafana is the source of truth for whether
the system is actually behaving:

{{include:verify-in-grafana}}

## Done

At this point the workflow is complete. A real skill should end with a
one-sentence "what success looks like" so Claude knows when to stop and
report back to the customer.
