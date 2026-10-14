# Scope and historical evidence

Resolve datasource, metrics cluster, Slurm deployment, and namespace as one
verified scope. Filter discovery by the user's cluster rather than listing every
cluster or trying combinations of namespaces and job IDs. For a discovered
`list_metric_label_values` schema that accepts `matches`, a cluster filter is:

```json
{"matches":[{"filters":[{"name":"cluster","type":"=","value":"<user's cluster>"}]}]}
```

Replace the placeholder and supply the other arguments required by the actual
schema. Verify the producer's namespace label; use `exported_namespace` only
when a scrape-label collision is established. Likewise, choose `exported_pod`
only for an established controller/exporter label collision.

Controller tools use the controller namespace. **Resource usage uses the compute
exporter's scrape namespace as `namespace`, and the controller namespace as
`controller_namespace`** to include syncer allocation. Confirm each separately;
do not silently switch deployments to get a nonempty answer.

For a current question, choose a recent bounded window and hold its end fixed
across related calls. For a historical question, use the time the user means,
not the current cluster snapshot. Ask for the time when it is essential and
unknown. Start with at most an hour around the event and widen deliberately
within the tool's documented limits and known retention. Queue is a single
`endTime` snapshot, not a `startTime`/`endTime` range.

Observe the result's comparison times: scheduling's `syncer_context_time` is the
last pending syncer observation when available; queue comparisons use a pending
observation near `endTime`; topology capacity is evaluated at `endTime`. These
are not automatically the same instant. Use compatible observations before
joining evidence. Samples can remain in the metrics backend after the live job
or controller disappears; retention is not a durable accounting guarantee.

Preserve original sample times, staleness, gaps, and conflicts. Fresh scraping
does not establish that the underlying event collector has fresh information.
Do not merge old and new controller identities or equate scheduler activity with
leadership. A permission error, successful empty result, unsupported tool, and
missing replay recording require different explanations.
