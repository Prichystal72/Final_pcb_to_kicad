"""Coordinate system conversion between pixel space (UI) and millimeter space (KiCad)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CoordinateSystem:
    """Handles pixel <-> mm conversion using a defined scale (DPI or px/mm).

    The KiCad coordinate origin is top-left, Y grows downward – same as Qt,
    so no axis flip is needed, only scaling.
    """

    pixels_per_mm: float = 10.0  # default – user sets via calibration

    # Optional board-level offset so the KiCad origin can differ from image (0,0)
    origin_offset_mm: tuple[float, float] = field(default_factory=lambda: (0.0, 0.0))

    # --- conversion helpers ------------------------------------------------

    def px_to_mm(self, px_x: float, px_y: float) -> tuple[float, float]:
        """Convert pixel coordinates to millimetres."""
        mm_x = px_x / self.pixels_per_mm + self.origin_offset_mm[0]
        mm_y = px_y / self.pixels_per_mm + self.origin_offset_mm[1]
        return (round(mm_x, 4), round(mm_y, 4))

    def mm_to_px(self, mm_x: float, mm_y: float) -> tuple[float, float]:
        """Convert millimetres to pixel coordinates."""
        px_x = (mm_x - self.origin_offset_mm[0]) * self.pixels_per_mm
        px_y = (mm_y - self.origin_offset_mm[1]) * self.pixels_per_mm
        return (px_x, px_y)

    def px_length_to_mm(self, px_len: float) -> float:
        """Convert a scalar length from pixels to mm."""
        return round(px_len / self.pixels_per_mm, 4)

    def set_scale_from_reference(self, px_distance: float, real_mm: float) -> None:
        """Calibrate scale from a known real-world measurement."""
        if real_mm <= 0 or px_distance <= 0:
            raise ValueError("Both distances must be positive.")
        self.pixels_per_mm = px_distance / real_mm
