# Camera iteration evidence, 2026-10-06

## Source candidate

The development candidate is on `codex/camera-iteration-contract`, based on
`3ff9a8428510dcadae4d4747db31bf2d79560f22` in the personal source repository.
It combines proportional authoring guidance with bounded, cancellable worker
admission and a [soft memory gate](../../../packages/cadgen/MEMORY.md).
It has not been promoted or installed. The marketplace-managed installation
remains `cad@homelab-plugins` 0.5.5. The canonical camera repository, Kingserver,
and the WSL configuration were not changed.

The [candidate validation receipt](candidate-validation.json) records source
file hashes and the focused checks that passed:

- 117 Windows tests covering pool/client/admission/memory and global skill,
  requirement, and package boundaries (1.059 seconds).
- 14 additional Windows singleton, channel-encoding, platform, and status tests
  (0.183 seconds).
- 23 native WSL job/retirement tests (14.226 seconds).
- A native WSL three-level nested build with one active slot (29.845 seconds).
- 16 native WSL store truth-table, closure, execution-hash, and publication tests
  (14.726 seconds); no store implementation was changed.
- 14 release-pin tests (0.509 seconds), using an isolated LF copy of the
  unchanged Bash script because the Windows checkout uses CRLF.
- All five edited skills passed the bundled official skill validator.
- Canonical `scripts/bundle/bundle.sh --check` passed; see
  [the final output](bundle-check.log). The initial Windows-line-ending failure
  is retained in [its log](bundle-check-windows-line-endings.log). All seven
  generated runtime files matched their committed Git blobs byte for byte;
  temporarily normalizing the comparison inputs to LF made the gate pass.
  All original working-tree bytes were restored. No generated runtime changed.

This is focused coverage, not a claim that the entire repository test suite or
a newly built installable wheel passed. Source code was selected through
`PYTHONPATH`, without installing the source package. The existing development
environment reports stale ignored distribution metadata `0.4.28`; tracked
release metadata remains 0.5.5. Source identity is the Git base and candidate
file hashes, not that environment's reported version.

## Representative camera edits

[The input manifest](camera-fixture-inputs.json) records 18 exact inputs from
canonical camera commit `d398c321e33696ec6e4e387f308e8c4a9fe7146a`.
The isolated copy and generated documents live under
`models/camera-iteration-validation/`, locally ignored. Checks ran sequentially
with one active job and no speculative spares. Source Python used the unchanged
committed Node runtime through `CADGEN_NODE_BUILDERS_DIR`.

[The run record](camera-fixture-runs.json) and linked per-case logs distinguish
the intended scope from the observed freshness verdicts:

| Case | Observed result |
| --- | --- |
| Baseline | Holder, plate, seating assembly, and full fit built with their declared STEP/3MF outputs |
| Unchanged full fit | Geometry stayed current; all four primary document hashes were unchanged |
| Holder clearance 0.20 to 0.25 mm | Holder and both fit assemblies rebuilt; plate stayed current; purchased references reused |
| Service-opening width 14 to 15 mm | Plate and full fit rebuilt; holder and seating assembly stayed current |
| Restore each edit | Normal builds restored exact baseline hashes of all four primary STEP documents |
| Deliberate pre-geometry holder failure | Attempt failed, primary artifact hashes stayed unchanged, restored source became current |

The first baseline-holder attempt failed during declared 3MF generation because
the live Node builder lacked local dependencies. Its STEP was valid but the job
was unsuccessful; [the failure log](baseline-holder.log) is retained. Selecting
the supported committed runtime resolved that environment issue. The deliberate
recovery test failed before geometry; it does not prove transactional rollback
for every later export or geometry failure.

[Geometry checks](camera-fixture-geometry.json) found one valid solid in each
baseline and changed fabricated part. Holder volume changed from
12148.03118766088 to 12013.056767575901 mm³; its outer bounds stayed
70.4 × 118.15 × 13 mm. Plate volume changed from 19649.313690310704 to
19601.313690310704 mm³, consistent with the 48 mm³ opening increase.
[Supporting-face measurements](camera-fixture-dimensions.json) directly checked
holder rear opening width 65.9 to 66.0 mm and plate opening 14 × 32 to
15 × 32 mm.

Six targeted snapshots were generated and visually reviewed: two changed-holder
views, two changed-plate views, and two restored full-fit views. The review
[receipt](snapshot-review.json) records local image hashes and source document identities. No new
visual concern was found in the retained walls, bosses, catches, opening, yoke,
or integrated factory shell. Changed fit assemblies were built but were not
separately snapshotted; the integrated visual review uses the restored baseline.
These are partial matrix evidence, not complete scenario or physical-fit approval.
The PNGs remain local ignored artifacts. Snapshot dependencies and extracted
browser libraries were isolated under source `tmp/`; no OS packages were
installed. The earlier browser-library failure remains in
[its log](camera-fixture-snapshots.log).

## Memory-aware source daemon

[A private fresh-store daemon run](source-daemon-runs.json) exercised the final
source candidate with four resident workers allowed, one active job, and zero
spares. The daemon and its descendants were owned by the check.

| Case | Elapsed seconds | Sampled process-tree peak RSS bytes |
| --- | ---: | ---: |
| Fresh holder and reference | 81.606 | 2,325,667,840 |
| Unchanged holder | 10.863 | 2,214,359,040 |
| Forced warm rebuild diagnostic | 22.595 | 2,266,013,696 |

The fresh run used two worker imports; subsequent checks imported no new
workers. Peak active jobs remained one. Every successful run produced exactly
the cold-baseline STEP/3MF hashes, with no swap or OOM events. The unchanged run
reported current geometry. [Orderly retirement](source-daemon-retirement.json)
confirmed daemon exit and no surviving observed owned processes.

The 100 ms RSS samples can miss brief peaks and count shared pages repeatedly.
These source runs used Windows-mounted paths and included the client plus
resident daemon tree; the earlier pilot used native Linux staging. They are not
a controlled throughput comparison. Admission estimates do not enforce an
allocation limit or guarantee that arbitrary future geometry fits the VM.

## Remaining acceptance and distribution work

The acceptance matrix still includes shared exterior growth, snap-interface and
coupled mounting changes, reference correction, independent study changes,
export-only work, and risky late geometry failures beyond the representative
checks above. Physical screws, snaps, USB/cable access, and printer/material
trials remain unverified. The imported display's documented self-intersection
does not exempt fabricated parts from validity checks.

Personal-fork review/merge, release-version selection, wheel validation,
marketplace promotion, installation, and release-closure validation remain
pending. Source validation alone does not establish that the user's installed
camera workflow has changed.

## Earlier installed-wheel pilot

The staged camera input remains canonical commit
`d398c321e33696ec6e4e387f308e8c4a9fe7146a`; all 514 staged input hashes were
verified before this check. Execution used the existing isolated WSL pilot and
the marketplace-managed 0.5.5 wheel with SHA-256
`b0d7cc8b77326f1460f359e2f2320e96e68983efe6ed3b7138fc496c346e5ae2`.
No canonical model or installed plugin was changed.

The normal, unchanged holder rerun used the same store as the earlier fresh
successful build. [The log](holder-current.log) reports `current`, not rebuilt.
[The measurement](holder-current.json) records 5.0549 seconds, sampled
process-tree peak RSS of 466,731,008 bytes (about 445 MiB), and zero swap/OOM
events. Both STEP and 3MF SHA-256 values exactly match the earlier baseline.
The early-kernel-import hint shows that a current rerun still pays import cost.

This proves same-session geometry reuse in that pilot. It does not prove source
runtime admission changes, cross-session reuse, assembly fit, or physical
fabrication acceptance. Sampled RSS can miss brief peaks and count shared pages
more than once; Windows headroom also includes concurrent system activity.

No new snapshot was generated because geometry was unchanged. The unchanged
hashes reuse the earlier valid-solid/count/volume/bounds comparison; they do not
add a new visual or physical-fit acceptance claim.
