"""
DWSIM/tests/test_txy_engine.py
================================
Pytest unit + integration tests for the Group 2 Txy engine.

All tests run in MOCK mode (no DWSIM required).
On the workstation with DWSIM, mock mode auto-disables and real BinaryEnvelopeUtility runs.

Run from repo root:
    python -m pytest DWSIM_ry_test/tests/test_txy_engine.py -v
"""

import sys
import os
import json

import pytest
import pandas as pd
import numpy as np

# ── resolve imports from repo root ──────────────────────────────────────────
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from DWSIM_ry_test.tasks.txy_engine import (
    validate_task,
    run_txy_task,
    _mock_txy,
    _auto_select_package,
    generate_txy_plot,
)


# ─────────────────────────────────────────────────────────────────────────────
#  FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def ethanol_water_task():
    return {
        "task_type":        "txy",
        "component_1":      "Ethanol",
        "component_2":      "Water",
        "pressure_Pa":      101325.0,
        "n_points":         15,
        "property_package": "NRTL",
    }

@pytest.fixture
def benzene_toluene_task():
    return {
        "task_type":        "txy",
        "component_1":      "Benzene",
        "component_2":      "Toluene",
        "pressure_Pa":      101325.0,
        "n_points":         10,
        "property_package": "PR",
    }


# ─────────────────────────────────────────────────────────────────────────────
#  1.  VALIDATION TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestValidation:

    def test_valid_task_passes(self, ethanol_water_task):
        validate_task(ethanol_water_task)   # must not raise

    def test_missing_component_2(self):
        with pytest.raises(ValueError, match="missing required keys"):
            validate_task({"task_type": "txy", "component_1": "Ethanol"})

    def test_wrong_task_type(self, ethanol_water_task):
        ethanol_water_task["task_type"] = "flash"
        with pytest.raises(ValueError, match="txy"):
            validate_task(ethanol_water_task)

    def test_n_points_too_small(self, ethanol_water_task):
        ethanol_water_task["n_points"] = 3
        with pytest.raises(ValueError, match="n_points"):
            validate_task(ethanol_water_task)

    def test_negative_pressure(self, ethanol_water_task):
        ethanol_water_task["pressure_Pa"] = -1000
        with pytest.raises(ValueError, match="pressure_Pa"):
            validate_task(ethanol_water_task)

    def test_invalid_property_package(self, ethanol_water_task):
        ethanol_water_task["property_package"] = "MAGIC"
        with pytest.raises(ValueError, match="property_package"):
            validate_task(ethanol_water_task)


# ─────────────────────────────────────────────────────────────────────────────
#  2.  PROPERTY PACKAGE AUTO-SELECTION
# ─────────────────────────────────────────────────────────────────────────────

class TestAutoPackageSelection:

    def test_polar_selects_nrtl(self):
        assert _auto_select_package("Ethanol", "Water") == "NRTL"

    def test_polar_case_insensitive(self):
        assert _auto_select_package("ethanol", "water") == "NRTL"

    def test_nonpolar_selects_pr(self):
        assert _auto_select_package("Benzene", "Toluene") == "PR"

    def test_mixed_polarity_selects_nrtl(self):
        # If either component is polar → NRTL
        assert _auto_select_package("Ethanol", "Hexane") == "NRTL"


# ─────────────────────────────────────────────────────────────────────────────
#  3.  MOCK Txy CALCULATION TESTS
#  Now checks the new schema: x1 | T_bubble_K | T_bubble_C | T_dew_K | T_dew_C
# ─────────────────────────────────────────────────────────────────────────────

class TestMockTxy:

    def test_output_is_dataframe(self):
        df = _mock_txy("Ethanol", "Water", 101325.0, 20)
        assert isinstance(df, pd.DataFrame)

    def test_correct_number_of_rows(self):
        for n in [5, 10, 20, 50]:
            df = _mock_txy("Ethanol", "Water", 101325.0, n)
            assert len(df) == n, f"Expected {n} rows, got {len(df)}"

    def test_required_columns_present(self):
        df = _mock_txy("Ethanol", "Water", 101325.0, 20)
        required = {"x1", "T_bubble_K", "T_bubble_C", "T_dew_K", "T_dew_C"}
        assert required.issubset(set(df.columns)), \
            f"Missing columns: {required - set(df.columns)}"

    def test_x1_spans_zero_to_one(self):
        df = _mock_txy("Ethanol", "Water", 101325.0, 21)
        assert abs(df["x1"].iloc[0])        < 1e-6, "First x1 should be 0"
        assert abs(df["x1"].iloc[-1] - 1.0) < 1e-6, "Last x1 should be 1"

    def test_bubble_below_dew(self):
        """At intermediate compositions bubble T <= dew T (two-phase region)."""
        df = _mock_txy("Ethanol", "Water", 101325.0, 21)
        mid = df[(df["x1"] > 0.05) & (df["x1"] < 0.95)]
        assert (mid["T_bubble_K"] <= mid["T_dew_K"] + 0.1).all(), \
            "Bubble point must be <= dew point"

    def test_T_K_and_T_C_consistent_bubble(self):
        df = _mock_txy("Ethanol", "Water", 101325.0, 10)
        diff = (df["T_bubble_K"] - df["T_bubble_C"] - 273.15).abs()
        assert diff.max() < 0.01, "T_bubble_K and T_bubble_C must differ by 273.15"

    def test_T_K_and_T_C_consistent_dew(self):
        df = _mock_txy("Ethanol", "Water", 101325.0, 10)
        diff = (df["T_dew_K"] - df["T_dew_C"] - 273.15).abs()
        assert diff.max() < 0.01, "T_dew_K and T_dew_C must differ by 273.15"

    def test_ethanol_boiling_point(self):
        """Pure ethanol (x1=1) bubble point near 78.4°C at 1 atm."""
        df = _mock_txy("Ethanol", "Water", 101325.0, 21)
        T_eth = df.loc[df["x1"] == 1.0, "T_bubble_C"].values[0]
        assert abs(T_eth - 78.4) < 3.0, f"Ethanol bp off: {T_eth:.1f}°C"

    def test_water_boiling_point(self):
        """Pure water (x1=0) bubble point near 100°C at 1 atm."""
        df = _mock_txy("Ethanol", "Water", 101325.0, 21)
        T_water = df.loc[df["x1"] == 0.0, "T_bubble_C"].values[0]
        assert abs(T_water - 100.0) < 3.0, f"Water bp off: {T_water:.1f}°C"

    def test_pure_components_bubble_equals_dew(self):
        """At x1=0 and x1=1, bubble and dew T must be equal (pure component)."""
        df = _mock_txy("Ethanol", "Water", 101325.0, 21)
        for x1_val in [0.0, 1.0]:
            row = df[df["x1"] == x1_val]
            assert abs(float(row["T_bubble_K"].values[0]) - float(row["T_dew_K"].values[0])) < 1.0, \
                f"Pure component at x1={x1_val}: bubble != dew"

    def test_benzene_toluene(self):
        """Benzene/Toluene should produce valid output."""
        df = _mock_txy("Benzene", "Toluene", 101325.0, 10)
        assert len(df) == 10
        assert df["T_bubble_C"].notna().all()
        assert df["T_dew_C"].notna().all()

    def test_higher_pressure_raises_bp(self):
        """At 2 atm the boiling points should be higher than at 1 atm."""
        df_1atm = _mock_txy("Ethanol", "Water", 101325.0, 10)
        df_2atm = _mock_txy("Ethanol", "Water", 202650.0, 10)
        assert df_2atm["T_bubble_C"].mean() > df_1atm["T_bubble_C"].mean()

    def test_unknown_components_fallback(self):
        """Unknowns get a safe linear fallback — should not raise."""
        df = _mock_txy("FakeCompA", "FakeCompB", 101325.0, 10)
        assert len(df) == 10


# ─────────────────────────────────────────────────────────────────────────────
#  4.  PLOT GENERATION TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestPlotGeneration:

    def test_plot_saves_png(self, tmp_path):
        df = _mock_txy("Ethanol", "Water", 101325.0, 20)
        png = generate_txy_plot(df, "Ethanol", "Water", 101325.0, "NRTL",
                                str(tmp_path), temp_unit="K")
        assert os.path.isfile(png), "PNG file not created"
        assert png.endswith(".png")

    def test_plot_celsius_unit(self, tmp_path):
        df = _mock_txy("Ethanol", "Water", 101325.0, 20)
        png = generate_txy_plot(df, "Ethanol", "Water", 101325.0, "NRTL",
                                str(tmp_path), temp_unit="C")
        assert os.path.isfile(png)

    def test_plot_benzene_toluene(self, tmp_path):
        df = _mock_txy("Benzene", "Toluene", 101325.0, 20)
        png = generate_txy_plot(df, "Benzene", "Toluene", 101325.0, "PR",
                                str(tmp_path), temp_unit="K")
        assert os.path.isfile(png)


# ─────────────────────────────────────────────────────────────────────────────
#  5.  FULL PIPELINE INTEGRATION TESTS (mock mode)
# ─────────────────────────────────────────────────────────────────────────────

class TestFullPipeline:

    def test_pipeline_returns_dataframe(self, ethanol_water_task, tmp_path):
        df = run_txy_task(ethanol_water_task, output_dir=str(tmp_path), plot=False)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == ethanol_water_task["n_points"]

    def test_csv_written_to_output(self, ethanol_water_task, tmp_path):
        run_txy_task(ethanol_water_task, output_dir=str(tmp_path), plot=False)
        csv = tmp_path / "txy_Ethanol_Water.csv"
        assert csv.exists(), "CSV output file not found"

    def test_csv_has_correct_columns(self, ethanol_water_task, tmp_path):
        run_txy_task(ethanol_water_task, output_dir=str(tmp_path), plot=False)
        df = pd.read_csv(tmp_path / "txy_Ethanol_Water.csv")
        required = {"x1", "T_bubble_K", "T_bubble_C", "T_dew_K", "T_dew_C"}
        assert required.issubset(set(df.columns))

    def test_png_written_to_output(self, ethanol_water_task, tmp_path):
        run_txy_task(ethanol_water_task, output_dir=str(tmp_path), plot=True, temp_unit="K")
        png = tmp_path / "txy_Ethanol_Water.png"
        assert png.exists(), "PNG output file not found"

    def test_auto_package_not_specified(self, tmp_path):
        """Task without property_package should auto-select NRTL for Ethanol/Water."""
        task = {
            "task_type":   "txy",
            "component_1": "Ethanol",
            "component_2": "Water",
            "pressure_Pa": 101325.0,
            "n_points":    10,
        }
        df = run_txy_task(task, output_dir=str(tmp_path), plot=False)
        assert len(df) == 10

    def test_invalid_task_raises_before_dwsim(self, tmp_path):
        """Bad task spec must raise before any DWSIM calls."""
        bad_task = {"task_type": "txy", "component_1": "Ethanol"}   # missing comp2
        with pytest.raises((ValueError, KeyError)):
            run_txy_task(bad_task, output_dir=str(tmp_path), plot=False)

    def test_benzene_toluene_pipeline(self, benzene_toluene_task, tmp_path):
        df = run_txy_task(benzene_toluene_task, output_dir=str(tmp_path), plot=False)
        assert len(df) == benzene_toluene_task["n_points"]
        assert df["T_bubble_K"].notna().all()
        assert df["T_dew_K"].notna().all()


# ─────────────────────────────────────────────────────────────────────────────
#  6.  EXAMPLE JSON FILES LOAD CORRECTLY
# ─────────────────────────────────────────────────────────────────────────────

class TestExampleJsonFiles:
    """Verify the example task files in DWSIM_ry_test/tasks/examples/ are valid."""

    EXAMPLES_DIR = os.path.join(
        os.path.dirname(__file__), "..", "tasks", "examples"
    )

    def _load(self, filename):
        path = os.path.join(self.EXAMPLES_DIR, filename)
        with open(path) as f:
            return json.load(f)

    def test_ethanol_water_json_valid(self):
        task = self._load("ethanol_water.json")
        validate_task(task)

    def test_benzene_toluene_json_valid(self):
        task = self._load("benzene_toluene.json")
        validate_task(task)

    def test_methanol_water_json_valid(self):
        task = self._load("methanol_water.json")
        validate_task(task)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
