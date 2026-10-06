# Isolated camera iteration fixture

This local fixture validates the source candidate without editing the canonical
`single-gang-tft-wall-panel` project. Its input authority is commit
`d398c321e33696ec6e4e387f308e8c4a9fe7146a`. The exact 18 staged inputs and hashes,
observed build scope, geometry measurements, and limitations are recorded in
[the evidence receipt](../../docs/evidence/camera-iteration-20261006/README.md).

The local input copy, stores, baseline and changed STEP documents, daemon state,
and six reviewed snapshots are ignored. They are not a portable committed
fixture or a new camera design. Recreating this check requires the owning
project's exact inputs from the manifest. Use one CAD operation at a time,
retain the same store for reuse checks, and preserve the canonical project.

Representative test edits changed holder clearance from 0.20 to 0.25 mm and
plate service-opening width from 14 to 15 mm. Both were restored through normal
builds; all four primary baseline STEP hashes returned exactly. A deliberate pre-geometry
failure checked last-good artifact preservation and recovery. These checks
cover a subset of the acceptance matrix, not physical fabrication acceptance.
