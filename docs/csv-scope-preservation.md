# CSV partition preservation

CSV files with a `slug` header are checked both during generation and matcher evaluation before persistence. Replacements cannot remove existing slug partitions or change rows outside the partitions declared by complete replacement records. Full-file replacements must retain every existing slug. Field-only literals retain the partition-presence check. This guard does not prove search semantics or authorize changing all rows of a declared partition.

Regex matching retains historical MULTILINE|DOTALL semantics for compatibility. Use `[^\r\n]*` for physical-line matching; a single match does not imply a small edit. Three production incidents replaced file tails during single-slug edits. The guard rejects those lost partitions regardless of match count. Legitimate removal of a whole partition is deliberately blocked; it requires a separately designed migration rather than an implicit bypass.
