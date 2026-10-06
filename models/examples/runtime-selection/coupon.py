"""Small independent fixture for installed-plugin runtime selection checks."""

from cadgen import build123d as bd
from cadgen import step, stl

WIDTH_MM = 20.0
LENGTH_MM = 30.0
THICKNESS_MM = 3.0
HOLE_DIAMETER_MM = 4.0


@step(out="../../tmp/runtime-selection-fixture/coupon.step")
@stl(out="../../tmp/runtime-selection-fixture/coupon.stl")
def coupon():
    return bd.Box(WIDTH_MM, LENGTH_MM, THICKNESS_MM) - bd.Cylinder(
        HOLE_DIAMETER_MM / 2.0, THICKNESS_MM + 2.0
    )


if __name__ == "__main__":
    coupon()
