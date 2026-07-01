# CoreWeave Object Storage Availability Zones

Use this reference when helping a customer choose a zone for their bucket. Place the bucket in the same zone as compute workloads for best performance.

---

## Supported zones

| Region | Zones |
|--------|-------|
| **US-CENTRAL** | 05A, 06A, 07A, 08A, 08B |
| **US-EAST** | 01A, 02A, 03A, 04A, 04B, 06A, 08A, 13A, 14A |
| **US-WEST** | RNO2A, 01A, 04A, 09B, 10A |
| **CA-EAST** | 01A |
| **EU-NORTH** | 05A |
| **EU-SOUTH** | 03B, 04A |

## Zone naming format

Full zone names combine region and suffix: `US-EAST-04A`, `US-CENTRAL-05A`, `EU-SOUTH-04A`.

## Guidance

- **Default recommendation:** `US-EAST-04A` — large zone with broad instance type availability.
- **If the customer has a CKS cluster:** use the same zone as their cluster. Check with `terraform output` or Console → Clusters.
- **If the customer is in Europe:** recommend `EU-SOUTH-04A` or `EU-NORTH-05A` depending on latency requirements.
- **If the customer needs specific GPU types:** the bucket zone doesn't need to match GPU availability, but co-locating reduces data transfer latency.
