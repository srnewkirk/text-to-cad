# CAD iteration contract

This specification defines the personal CAD plugin improvement effort. The
product goal is stable, understandable iteration: complete coherent design
changes with correct, reviewable results, explainable build scope, and a
recoverable last-good state. Maximal build throughput is secondary.

## Ownership and acceptance target

- Source owner: `srnewkirk/text-to-cad`, locally
  `C:\Users\srnew\source\repos\text-to-cad`.
- Distribution owner: `srnewkirk/codex-plugin-marketplace`, locally
  `C:\Users\srnew\source\repos\codex-plugin-marketplace`.
- Installed identity at the start of this effort: `cad@homelab-plugins`, private
  0.5.5. Source development does not update that installation.
- Primary acceptance fixture: the actively developed camera display in
  `C:\Users\srnew\source\repos\single-gang-tft-wall-panel`.
  Its `docs/projects/single-gang-camera-display/current-design.md` owns design
  requirements and status. The completed sensor display is a secondary example,
  not the rollout target or a model to redesign.

The user authorizes using the `single-gang-tft-wall-panel` project for camera
model changes. Model edits belong in that owning repository on a development
branch or worktree, following its `AGENTS.md` and current-design authority.
Preserve unrelated work and the recoverable accepted baseline. This permission
does not select a new geometry requirement: use agreed design intent for product
edits and identify representative test edits as tests.

Standalone plugin fixtures and their generated CAD artifacts belong under this
source repository's `models/` area. Keep fixtures focused; do not copy or hydrate
an entire model catalog. Capture the exact source revision and required input
hashes when establishing a baseline. Preserve existing pilot work and use it as
evidence, not as an implicit production implementation.

## Required behavior

### Installed runtime selection acceptance — corrected 2026-10-06

Private 0.5.6 is released and installed. Its admission and incremental-workflow
changes remain valid; release closure did not prove the ordinary project's
runtime selection. The camera's later project-owned WSL setup is a working
prototype and evidence, not the reusable plugin implementation.

On this user's configured Windows/Ubuntu WSL2 host, ordinary installed-plugin
CAD use must select the marketplace-managed Linux runtime for source execution,
document inspection, exports, snapshots and Viewer. Runtime identity must be
observable. Configured-but-unavailable WSL must fail with an actionable repair
instruction, without silently selecting Windows. Native Linux/macOS remain
native; this requirement is not a general remote execution platform.

Ownership is explicit:

- `text-to-cad` owns reusable runtime discovery, setup, invocation, diagnostics
  and skill guidance. A host launcher wraps Python execution; model scripts
  remain programs and document CLIs never become model-build doors.
- The personal marketplace owns verified private wheel delivery and its thin
  installation adapter. No source/worktree plugin installation or editing of
  the installed cache is an acceptance shortcut.
- Consumer projects own model sources, workspace/import paths and model-specific
  dependencies. A thin adapter may call the installed plugin; projects must not
  duplicate generic runtime management or hardcode its release internals.

Keep the 4 GB WSL memory, four CPUs, 2 GB swap envelope and Kingserver unchanged.
Keep one active geometry job, bounded cancellable admission, separate resident
limits and zero speculative spares. Do not weaken allocation policy to make a
test pass or infer a speed guarantee from successful Linux execution.

The smallest implementation sequence is shared helper and meaningful routing/
setup/diagnostic tests; marketplace bootstrap integration; sequential camera and
tiny independent fixture checks; then review for managed promotion. Preserve
working project setup until its replacement is verified. No broad revert,
blanket rebuild or premature release is required.

Final acceptance asks whether a fresh CAD session uses the **installed plugin's
normal entry points** to author, build, inspect and review one coherent change
in both fixtures without bespoke runtime setup. Check Linux identity, managed
wheel provenance, rendering dependencies, explainable affected builds, useful
failure diagnosis, reuse and last-good preservation. Candidate/package tests
and installed-workflow acceptance are separate gates. Until managed promotion
and those fresh-session checks pass, report implementation as uninstalled.

The seven modeling requirements below remain in force. Existing receipts cover
representative holder/plate increments, validity, snapshots, reuse, failure
preservation and bounded admission; the matrix records the remaining subset.
Do not make every possible matrix case a new prerequisite for this routing fix.
Physical screw/snap/cable fit and strength remain physical-trial questions.

1. **Coherent increments.** Choose the smallest design change that produces a
   useful, reviewable result. Its boundary follows intent, coupling, and
   uncertainty. Batch predictable coupled edits, such as holes, bosses, and
   counterbores. Validate risky geometry before adding unrelated changes.
   Complete the affected edit/build/inspect loop before the next unrelated
   increment; no mandatory commit, report, or user approval after every step.
2. **Understandable units.** Fabrication models represent parts we make, with
   purpose-named features and explicit inputs. Reference components represent
   purchased or external constraints to the fidelity needed for fit. Correct a
   reference or select another component without redesigning the purchased
   component as a manufactured part. Assemblies combine both for human review.
   Features need not become separate files, exported solids, or jobs.
3. **Authoritative interfaces.** Define a shared mating interface once and
   consume it from both sides. A changed interface affects its mating parts
   and their fit checks; unrelated components remain reusable. Do not create
   duplicated constants or general frameworks to enforce this principle.
4. **Honest rebuild scope.** Distinguish logical features from runtime build
   units. Explain invalidation using actual model, value, file, and imported
   document dependencies. Feature naming alone does not provide feature-level
   caching. Investigate unexpected scope, expensive geometry operations,
   duplicated work, and accumulated change before raising resource limits.
   High cost alone is not proof of a bad design.
5. **Proportional validation.** Reuse applicable evidence and valid outputs;
   check changed units and interfaces, then required integration and fabrication
   conditions. Review visibly changed primary geometry with a targeted snapshot.
   Avoid repeated snapshots, redundant exports, directory-wide build sweeps, and
   whole-project rebuilds without a task-specific reason. Export-only requests
   reuse valid geometry. Declared model outputs still follow the runtime
   contract; deferring optional export work must not silently skip declarations.
6. **Recoverable failures.** Retain enough source and valid artifacts to recover
   the last-good result using ordinary project tools. Localize a failed feature,
   repair the responsible source, and rerun affected checks. Never report a
   partial or stale artifact as the successful result of a failed attempt.
   This does not require a new checkpoint service or a commit per increment.
7. **Productive resource waiting.** Reclaim owned idle resources. Report the
   reason for admission waiting and allow cancellation while waiting. Wait when
   active work can release capacity; detect lack of productive progress and
   report inability to admit under the current policy instead of waiting
   forever. Parent/child dependencies must not deadlock. Releasing a parent's
   job slot does not imply releasing its resident worker or memory. Admission
   estimates do not guarantee that later kernel allocations fit.

Apply the authoring loop to source creation and modification. Read-only document
inspection, reference selection, and fabrication handoffs retain their own
appropriate workflows. A document reader must not rebuild source implicitly.

## Camera acceptance matrix

These are acceptance criteria for isolated fixtures or a camera development
branch/worktree. The [evidence receipt](evidence/camera-iteration-20261006/README.md)
records the covered subset and remaining cases; this matrix itself is not a
claim that every case passed. Record expected and observed affected units
separately. Any extra
invalidation needs a concrete dependency explanation and an assessment of
whether a small source-boundary correction is warranted.

| Scenario | Observable pass evidence | Failure signal or explicit limit |
| --- | --- | --- |
| Holder clearance or rear-extension change | Holder and consuming fit assemblies update; measured seating/clearance facts agree with the chosen edit; plate stays current when its consumed values are unchanged | Unexplained plate or reference rebuild; rear extension also changes display placement, so verify that placement |
| Coupled mounting-pattern, pocket, boss, and bore change | Centers, depths, backing, and relevant clearances agree across the coherent edit; affected holder and assembly checks pass | Partial update or contradictory datums; the retained display mounting constraints cannot be changed by redesigning the reference |
| Holder exterior growth | Holder outline and plate flange update together; snap datums and purchased reference remain unchanged | Flange mismatch or snap profiles inadvertently scaled; this is intentionally a two-part change |
| Snap interface change | Both mating fabrication parts and consuming fit assemblies use the same updated definition; unchanged reference geometry is reused | One mating side remains stale; static fit does not establish flex, strength, or printed engagement |
| Plate service-opening or yoke change | Plate and full fit assembly update; holder and focused display-seating assembly stay current | Unexplained holder invalidation or lost wall-interface dimensions |
| Independent front-layout study change | Only the drawing study and its required output/review update | 3D models rebuild; this non-manufacturing study currently has separate dimensions, so no automatic 3D synchronization is claimed |
| Purchased display reference correction | Documented reference changes reach actual geometry consumers and their fit checks; unaffected plate geometry is reused | Reference is treated as a fabricated redesign; preserve documented imported-geometry limitations |
| Risky shell, boolean, or fillet fails | Failure identifies responsible geometry; last-good source/artifacts remain recoverable; local repair passes affected checks | Misleading success or loss of the recoverable good result |
| Unchanged rerun and export-only request | Freshness verdict and job/output evidence show geometry reuse; only necessary export or document-compilation work occurs | Unexplained geometry regeneration; a cold store may legitimately need document compilation |
| Resource contention and nested builds | Releasable contention reports waiting and progresses; waiting cancellation completes; non-progressing admission terminates clearly; good artifacts survive | Deadlock, indefinite wait, unrelated process termination, or unexpected OOM; test controlled contention rather than deliberately exhausting the laptop |

For each changed geometry case, include validity, relevant measurements,
targeted visual review, and required parent integration. Use clearances and
tolerances from the fixture's design brief; do not invent universal limits.
Record build outcomes, actual affected units, export/snapshot work, elapsed
time, and sampled resource use where relevant. A faster result with incorrect
fit fails; repeated unnecessary work also fails the efficiency criterion.

The current camera has two printed parts, retains the factory display shell and
buttons, and has no additional front bezel. Its imported display has a documented
self-intersection limitation. Cable dimensions, physical screw fit, printed
snap behavior, and printer/material trials remain separate acceptance questions.
Plugin tests must not silently convert those limitations into physical-fit
claims or blanket validation exemptions for fabricated parts.

## Existing evidence and limits

At the initial review on 2026-10-06, source `main` was clean at `3ff9a842`;
the clean `codex/wsl-camera-pilot` worktree was at `6fb942db`. Its
`docs/wsl-camera-pilot.md` and linked receipts document a fresh camera-holder
build in 47.889 seconds, sampled process-tree peak RSS of 1.03 GiB, and zero
swap/OOM events. A separate comparison matched valid solid count, volume, and
bounds. These measurements apply only to that holder, not whole assemblies or
visual, cable, and physical-fit acceptance. Sampled RSS is not an enforced peak.

A subsequent [unchanged-holder check](evidence/camera-iteration-20261006/README.md)
verified same-session `current` reuse in 5.0549 seconds with about 445 MiB
sampled peak RSS and unchanged STEP/3MF hashes. It still paid early kernel
import cost. This is installed-wheel pilot evidence, not validation of a source
runtime change.

The configured aggregate WSL2 envelope is 4 GB memory, four CPUs, 2 GB swap,
with `autoMemoryReclaim=dropCache`. It includes Docker Desktop. Keep this envelope
and Kingserver unchanged. The pilot serializes operations and reuses a
same-session store. The source candidate adds measured soft memory admission;
reuse across restaged session paths remains unverified. The obsolete 4 GiB-free
gate is not a requirement.

Starting source `3ff9a842` deliberately failed at resident-worker capacity rather
than waiting behind active builds. The development candidate adds bounded,
cancellable admission waiting while active work, imports, or owned teardown
can release capacity. Tests distinguish productive work from retained parents
that have yielded their active slot. Job-slot queuing and resident-worker
admission remain different mechanisms. Cached community `upstream/main` at
`4eaf7459` is read-only comparison evidence; verify any comparison ref before
use and never call it the latest upstream implementation without checking.

Source-candidate retirement and ledger tests passed in the existing native WSL
environment (23 tests), as did a three-level nested build with one active slot.
The source was selected through `PYTHONPATH`, without changing the installed
plugin. A fresh source worker used 277,360,640 bytes of idle RSS in one WSL
sample and exited after the check. That is import-footprint evidence, not a
geometry working-set guarantee. The memory-admission increment retained those
properties in focused tests and a fresh-store camera daemon run. It checks
Linux available memory and visible cgroup headroom, and preserves Windows
physical/commit limits. See the receipt for exact test coverage, geometry reuse,
resource observations, and remaining matrix cases. This is source-candidate
evidence; marketplace promotion and installation remain pending.

## Bounded implementation sequence

1. **Workflow guidance.** Update `skills/cad/SKILL.md`, its project-layout,
   generation, repair, review, and export references where behavior is affected.
   Replace blanket broad parallel-build recommendations with affected-target
   iteration and explicit resource considerations. Reconcile only applicable
   guidance in DXF, robot, and Viewer skills; preserve required validation and
   fabrication handoff checks. Exit evidence: a reviewed, consistent edit/build/
   inspect workflow that accurately describes existing runtime capabilities.
2. **Focused fixture evidence.** Establish the unchanged camera baseline and
   small representative changes from the matrix, using the owning camera
   project's development branch/worktree or isolated plugin fixtures as
   appropriate. Build one operation at a time on the laptop, reusing the same
   valid store. Use lightweight cases for invalidation and failure behavior
   where they answer the question; retain
   camera integration checks. Exit evidence: observed build scope, reuse,
   recovery, and measurement limits identify demonstrated gaps rather than
   assumed ones. Do not make cross-session cache redesign part of this step.
3. **Runtime admission and demonstrated gaps.** Review the existing pool,
   job-slot/dependency handling, cancellation, and owned-worker lifecycle.
   Assess relevant read-only upstream implementation before choosing a narrow
   personal-fork change. Add focused tests for releasable contention, idle
   reclamation, waiting cancellation, nested progress, non-progressing
   admission, and artifact preservation. Exit evidence: deterministic tests
   cover those states; sequential measured camera checks preserve correctness.
   Scope is existing runtime mechanisms, not a new scheduler platform or a
   predictor promising that arbitrary geometry fits memory.
4. **Integration and distribution readiness.** Review exact source changes;
   run affected tests and required package/bundle checks for the touched
   surfaces. Reconcile skills with verified behavior and retain final camera
   integration and fabrication validation. Produce a concrete marketplace
   promotion candidate with clean source/destination trees. Registration,
   installation, release, and other administrative actions remain separate
   explicitly authorized operations. A declared multi-repository release is
   complete only after the marketplace release-closure checker passes.

The coordinator owns criteria, scope, architecture, integration, and evidence
review. Use bounded lower-cost workers when useful, normally one execution
worker at a time; never run simultaneous CAD builds on this laptop.

The development effort is ready for the user's next camera edits only when the
required workflow and runtime behaviors have supporting evidence and the
validated candidate has reached the marketplace-managed installation. A saved
plan, a source-only fix, or the single-holder pilot alone does not meet that
readiness boundary. The user's upcoming design changes need not be available
before testing: representative model edits can establish readiness first.
