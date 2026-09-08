# WorkAsset Portability Policy

Portability answers a specific question:

> Can this method travel with the professional without taking employer-owned or
> customer-private material?

## Classes

| Class | Meaning | Default Action |
|-------|---------|----------------|
| `portable_method_review_required` | Metadata suggests a reusable method, but a human should review context before external use | Default for generated candidates |
| `portable_method` | Reviewed and safe to present as a professional method | Future promotion workflow |
| `restricted_context` | Depends on private project, employer, or customer context | Do not expose outside approved consumer scope |
| `non_portable` | Cannot be separated from private or employer-owned material | Do not export as reusable method |

## Default Generated Policy

apatch-generated candidates use the conservative default:

```json
{
  "content_safe": true,
  "exports_code": false,
  "exports_private_prompts": false,
  "exports_credentials": false,
  "exports_ssh_topology": false,
  "raw_logs": false,
  "portability_class": "portable_method_review_required",
  "rights_claim": "evidence_record_not_ownership_transfer",
  "employer_review": "required_when_private_context_or_contract_applies",
  "redaction": "strict"
}
```

## Review Standard

A reviewer should mark an asset restricted or non-portable when:

- the method cannot be described without naming private customer systems;
- the value depends on closed employer data or proprietary architecture;
- the proof trail would disclose confidential incident details;
- an employment or client contract reserves the method.

The WorkAsset can still exist as local evidence. The portability class controls
where it may be shown or reused.
