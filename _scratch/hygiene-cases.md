# Warn-tier cases (planted — see README.md)

Ordinary prose, outside any corpus. Every identifier below is synthetic
or unroutable; see the safety table in `README.md`. Each item in the
first section should produce exactly one **warning**, and the run should
still exit 0.

## Should be flagged

Email at a private-use TLD: ping ops@acme.internal when the run finishes.

An unroutable address: the node came up at 100.64.0.1 overnight.

A ticket whose project key is not one of ours: tracked in ACME-4471.

A bare internal handle: ask the folks @coreweave.com about quota.

An account identifier: run id 3fa85f64-5717-4562-b3fc-2c963f66afa6.

A console URL carrying an org: https://console.coreweave.com/#/orgs/acme-prod-1234/clusters

## Should NOT be flagged

These are the false positives the rules are shaped to dodge. If any
starts producing a finding, that is a regression worth chasing — they
are the reason the gate is trusted enough to run on every PR.

**Reserved documentation values.** Deliberately here rather than in the
section above: these are the obvious things to reach for when writing an
example, so the allowlist covers them, and that coverage is worth
testing. It is also why the positives above had to use a private-use TLD
and CGNAT space instead — the safe-to-write values are exactly the ones
correctly suppressed.

- RFC 2606 domains: mail ops@example.com or someone@sub.example.org.
- RFC 5737 TEST-NET: 203.0.113.42 and 198.51.100.7.

> **KNOWN GAP, found by this testbed.** The subdomain case
> (`someone@sub.example.org`) IS currently reported. RFC 2606 reserves
> the whole domain including subdomains, so it should not be. The
> allowlist entry anchors the bare `example.com|org|net` and only allows
> a subdomain prefix on the `test|invalid|localhost` branch. Hoisting
> the prefix so it applies to both branches fixes it, and still reports
> the lookalikes `x@attacker.example.com.evil.io` and
> `x@evil-example.com` — verified. Impact is one spurious *warning*, not
> a block, so it is not urgent. Delete this note when it is fixed.

**Product vocabulary.** Instance types and zones: a `gd-8xh100ib-i128`
node in US-EAST-04A. Model names with versions: serve Llama-3 or
TinyLlama-1 on a cpu-4 pool.

**Standards and specs:** TLS-1 over SHA-256, encoded UTF-8, per IEEE-754.

**Our own project keys** in maintainer notes: implements APPSEC-3972
(TM-013).

**Private networks written as CIDR:** pod range 10.0.0.0/13, services
10.16.0.0/22, and the metadata endpoint at 169.254.169.254. Note a bare
private host address is NOT covered — the `/mask` is the discriminator,
because a network definition is documentation while a lone host address
is what a real customer node looks like.

**Git config syntax:** the `url.git@github.com:.insteadOf` rewrite.

**The published contact address** devx@coreweave.com in a plugin manifest.

**Typography** — an em-dash — and an ellipsis… both appear constantly in
this repo's prose, so neither may ever be treated as paste residue.
