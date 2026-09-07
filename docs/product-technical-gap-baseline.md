# BandScope product–technical gap baseline

## Goal and loop

BandScope must turn a selected song into a trustworthy, local-first rehearsal
map without granting an input string more file-system authority than the user
selected. The delivery loop is current evidence → RED contract → smallest owner
repair → full exact-head validation → protected merge.

## Product and technical evidence

- PRD/product boundary: `AGENTS.md` and `docs/brand-story.md` define a practical
  rehearsal workspace rather than a generic audio analyzer.
- TRD/security boundary: `docs/security/app-security.md` and
  `docs/doctoring/cli-job-file-authority.md` define untrusted CLI/file input and
  fail-closed handling.
- Architecture: `ARCHITECTURE.md` keeps the CLI as an adapter and the analysis
  API as the orchestration owner.
- Exact change lineage: PR
  [#811](https://github.com/ContextualWisdomLab/bandscope/pull/811), protected
  `develop@314ddeae7b775a4957594b599358c8255617eb2`, non-force reconciliation
  `9c44237803ab77f91bf88876ef88ab95a16fbf9c`, and naming RED
  `6591d34d63ad9c8344c6a43aeb4eb564d9ab96fd`.

## Context Map and model

```text
Desktop/Tauri ── JSON job contract ──> Analysis CLI adapter
                                           │ bounded bytes + file authority
                                           ▼
                                  Analysis orchestration API
                                           │
                       sections / harmony / roles / persistence
```

No ERD change is required: this slice changes neither stored records nor schema,
indexes, constraints, transactions, or migrations. External JSON keys such as
`jobId`, `request`, and `state` stay at the compatibility boundary; internal
Python identifiers use the CLI job-input ubiquitous language.

## Gap and action status

| Gap | Action | Status |
| --- | --- | --- |
| Unbounded or authority-bearing CLI input | Bound reads and reject unsafe path namespaces before lookup | Implemented in Draft PR #811; exact-head gates required |
| Generic private identifiers obscure the file-authority sequence | Rename the complete private caller surface and enforce it with an AST regression | Local GREEN: CLI slice 113 tests; full engine 100% coverage |
| Retired CLI analyzer hook keeps stale tests authoritative | Remove the hook and test orchestration delegation instead | Local GREEN: orchestration delegation and full suite |
| Branch predates protected workflow and dependency baseline | Merge current `develop` through ordinary two-parent history | Implemented; fresh exact-head checks required |
| Documented root-level coverage command measures the wrong module path | Run the coverage target from `services/analysis-engine` and update operator docs | Repaired; 764 passed, 24 native-parity skips, 100% coverage |

The PR remains Proposed/Draft until fresh repository and central security checks
finish successfully and an independent current-head approval satisfies ordinary
branch protection.
