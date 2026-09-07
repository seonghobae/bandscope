# Product technical gap baseline

## Goal and evidence boundary

BandScope's current dependency-runtime goal is a reproducible desktop build on the supported Node 22 line without weakening supply-chain evidence. This baseline is derived from protected `develop`, the active canonical compatibility pull request, repository contracts, and exact toolchain execution. A branch or pull request is Proposed evidence until ordinary protected-branch integration.

## Product and technical context

- **PRD:** a contributor or release operator can install, test, build, and package BandScope from one reviewed workspace lock without manual repair.
- **TRD:** Node `>=22.22.2 <23`, npm `10.9.9`, lockfile version 3, frozen `npm ci`, registry SRI, and generator-sensitive peer metadata are one compatibility contract.
- **Context Map:** Dependency Intent (`package.json` manifests) supplies the Lock Generation context; CI and Release consume only its committed artifact. npm registry data is external and remains behind npm's resolver boundary.
- **UML/runtime flow:** manifests -> pinned npm generator -> complete lock artifact -> frozen install -> lint/type/test/build/Storybook/Tauri -> release evidence.
- **ERD/persistence:** this slice changes no product database, table, column, index, constraint, sequence, view, function, ORM mapping, or data migration.

## Gap register

| Gap | Evidence | Action | Status |
| --- | --- | --- | --- |
| Node/jsdom manifest and lock disagreement | Runtime contract requires Node `22.22.2` and jsdom `30.0.1`; the predecessor lock retained Node `22.13` and jsdom 29 | Regenerate the complete root lock with Node `22.22.2` and npm `10.9.9` | Implemented locally; exact-head gates required |
| ESLint update lost generator-sensitive peer metadata | The predecessor dependency PR removed `peer: true` from platform-specific root `@esbuild/*` records | Integrate its manifest intent into the canonical lock owner and regenerate instead of transplanting its lock | Implemented locally; exact-head gates required |
| Dependency ownership was split across overlapping PRs | jsdom/Node and ESLint both write the root workspace lock | Preserve both histories through a two-parent non-force reconciliation in the canonical lock owner | Implemented locally |
| Runtime tests inspected the wrong ownership scope | One assertion searched job-local text for a workflow-global environment value; another rejected transitive packages' valid engine ranges | Assert the global workflow contract globally and the root package engine only at the root lock record | Implemented locally |
| Buyer-visible reproducibility remains unproven remotely | Local generation cannot prove hosted runners, platform builds, security scans, or independent review | Require unchanged-head CI, Windows, macOS, security, SBOM, CodeQL, release, and review evidence | Open |

## Invariants and rollback

The root `package-lock.json` remains the sole npm workspace lock. Manifest intent and the complete generated lock move together; partial lock transplantation and hand normalization are forbidden. A failed frozen install, integrity check, peer-metadata check, platform build, or security gate fails closed.

Rollback restores the previous manifests and their complete lock artifact together, preserves generator/runtime/check evidence, and reruns every exact-head gate. No database rollback is required.
