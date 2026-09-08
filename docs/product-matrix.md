# APatch delivery and integration boundary

Updated for the public OSS source candidate, 2026-09-08.
This describes existing delivery boundaries, not a new product strategy or a pricing plan.

| Surface | Role | Included in this repository |
| --- | --- | --- |
| APatch OSS runtime | Local governed execution, CLI, full professional MCP API, verification, recovery, evidence and optional integration adapters | Yes |
| **APatch Studio OSS** | Local user interface and optional client for connected work | Separate delivery |
| **APatch Studio Pro** | Commitment agreement, execution oversight and acceptance within TrustChain Cowork project context | No hosted Pro implementation |
| TrustChain Cowork / Platform | Shared projects, people and agents, context and service authority | External service |
| HC Tracker / Avatar | Optional professional-history, time and evidence integration | APatch-side adapters only; external peer/service delivered separately |

APatch 0.8.42 is already published under MIT. This clean source candidate prepares 0.8.43.
Publishing this runtime does not qualify or release the Studio UI or the hosted Cowork assembly.

## Shared engineering quality

OSS retains the full professional MCP API, governed sessions, path leases, rollback,
fixed verification, signed evidence, local extensions and remote execution support.
Verification quality and recovery are not made weaker to manufacture an upgrade boundary.
Strict correctness controls remain opt-in and require an explicitly configured contract.

## Locality and consent

Source files and local work artifacts accumulate on the user's machine. Standalone operation
does not require an account or subscription. Users may connect selected services and export
only material they permit; connecting does not transfer their local history or its ownership.

The selected Project Group remains the canonical collaboration context. APatch must not
create a second project registry, acceptance authority or payroll ledger.
A factual timesheet is not a payment decision; technical attestation is not business acceptance.

The Pro activation client from private development branches is not part of this OSS snapshot.
A future change to that publication boundary requires an explicit decision.
