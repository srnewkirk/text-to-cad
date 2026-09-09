# thang010146 models

Every script here is a thin `@step` wrapper: it reads the vendor document in
`STEP/imported/` with `cadgen.read_step` and re-exports it to `STEP/` with the
mechanism's kinematics and its clip. The geometry is the vendor's; the mates,
poses, and choreography are ours.

| Script | Artifact | DOFs / poses | Clip |
|---|---|---|---|
| `flip_mechanism_180.py` | `STEP/flip_mechanism_180.step` | `crank`, `coupler`, `rocker`; poses `rest`, `quarter`, `over_center`, `three_quarter`, `flipped` | `flip` (5 s) |
| `adjustable_height_table_2.py` | `STEP/adjustable_height_table_2.step` | `hoist`, `rise`, `descend`, four roller sliders, `actuator_rod`, `actuator_slider`, coupling `scissor`; poses `collapsed`, `mid`, `raised` | `lift` (8 s) |
| `gear_rack_gripper.py` | `STEP/gear_rack_gripper.step` | `left_pinion`, `right_pinion`, `left_jaw`, `right_jaw`, `piston`, two conrods, coupling `grip`; poses `closed`, `half_open`, `open` | `drive` (6 s) |

Build: `python src/<script>` per row; unchanged models are no-ops. The imported
document is a recorded build input, so replacing it makes its model stale on its
own.

Each script's `<name>.anim.js` sits beside it and is named by that script's
`animation=`; its text is copied into the artifact's sidecar at build.
