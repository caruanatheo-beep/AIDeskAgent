from src.analytics import curve_spreads, narratives, points_frame, talking_points
from src.data import SERIES
from tests.sample_data import sample_points


def test_curve_spreads_are_calculated_in_basis_points():
    spreads = curve_spreads(sample_points())
    assert spreads["US 2s10s"] == -47.0
    assert spreads["US 5s30s"] == 12.0


def test_transformations_keep_dates_and_statuses():
    frame = points_frame(sample_points())
    assert len(frame) == len(SERIES)
    assert {"Indicator", "Latest", "Change", "Observation", "Status"}.issubset(frame.columns)
    assert (frame["Status"] == "latest available").all()


def test_narratives_and_talking_points_are_labelled():
    assert narratives(sample_points())
    drafts = talking_points(sample_points())
    assert len(drafts) == 3
    assert all("human approval required" in item for item in drafts)
