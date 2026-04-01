"""
DWSIM_ry_test/tests/test_flash_engine.py
==========================================
Pytest tests for the Flash Flowsheet engine (no valve version).

Flowsheet: Feed → Cool-1 → FFEED → Flash1 → V + L
4 result streams: feed, cooler_out, vapor, liquid

Professor's validated reference (H2/CH4/Benzene/Toluene, Peng-Robinson):
    Feed:        410 K, 3447370 Pa, all vapor
    Cooler out:  320 K (46.85°C), removes 10345.38 kW, V_frac ≈ 0.906
    Flash:       320 K / 3350000 Pa
    V:           5074.37 kmol/h, fully vapor
    L:           522.62  kmol/h, fully liquid
    V composition (approx): CH4=0.657, H2=0.330, Benzene=0.011, Toluene=0.002
    L composition (approx): CH4=0.844, H2=0.008, Benzene=0.096, Toluene=0.053

Run from repo root (dwsim_ai/):
    python -m pytest DWSIM_ry_test/tests/test_flash_engine.py -v
"""

import sys
import os
import json

import pytest
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from DWSIM_ry_test.tasks.flash_engine import (
    validate_flash_task,
    run_flash_task,
    _mock_flash,
    _parse_components,
    _auto_select_package,
    _normalize_package,
    results_to_dataframe,
    print_flash_summary,
)

EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "tasks", "examples")


# ─────────────────────────────────────────────────────────────────────────────
#  FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def professor_task():
    """Exact JSON from professor's feedback PDF."""
    return {
        "components": {
            "Hydrogen": 0.3,
            "Methane":  0.6,
            "Benzene":  0.075,
            "Toluene":  0.025,
        },
        "pressure_Pa":      3447370.0,
        "temperature_K":    410.0,
        "flash_type":       "PT",
        "property_package": "Peng-Robinson",
        "cooler_temp_K":    320.0,
        "flash_pressure_Pa": 3350000.0,
        "feed_flow_molh":   5596990.0,
    }

@pytest.fixture
def ethanol_water_task():
    return {
        "components": {"Ethanol": 0.4, "Water": 0.6},
        "pressure_Pa":   300000.0,
        "temperature_K": 340.0,
        "property_package": "NRTL",
        "cooler_temp_K": 320.0,
        "flash_pressure_Pa": 101325.0,
        "feed_flow_molh": 100.0,
    }

@pytest.fixture
def three_component_task():
    return {
        "components": {"Ethanol": 0.3, "Water": 0.5, "Acetone": 0.2},
        "pressure_Pa":   400000.0,
        "temperature_K": 360.0,
        "property_package": "NRTL",
        "cooler_temp_K": 320.0,
        "flash_pressure_Pa": 101325.0,
        "feed_flow_molh": 200.0,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  1.  JSON PARSING — professor's dict format
# ─────────────────────────────────────────────────────────────────────────────

class TestParsing:

    def test_dict_format_parses(self, professor_task):
        comp_list, comp_dict = _parse_components(professor_task)
        assert comp_list == ["Hydrogen", "Methane", "Benzene", "Toluene"]
        assert abs(comp_dict["Hydrogen"] - 0.3) < 1e-9
        assert abs(comp_dict["Methane"]  - 0.6) < 1e-9

    def test_list_format_parses(self):
        """Old list+feed_composition format still accepted."""
        task = {
            "components":       ["Ethanol", "Water"],
            "feed_composition": {"Ethanol": 0.4, "Water": 0.6},
            "pressure_Pa":      101325.0,
            "temperature_K":    340.0,
        }
        comp_list, comp_dict = _parse_components(task)
        assert comp_list == ["Ethanol", "Water"]
        assert abs(comp_dict["Ethanol"] - 0.4) < 1e-9

    def test_invalid_components_type_raises(self):
        task = {"components": "Ethanol,Water", "pressure_Pa": 1e5, "temperature_K": 300}
        with pytest.raises(ValueError, match="dict"):
            _parse_components(task)


# ─────────────────────────────────────────────────────────────────────────────
#  2.  PACKAGE NORMALIZATION
# ─────────────────────────────────────────────────────────────────────────────

class TestPackageNormalization:

    def test_peng_robinson(self):
        assert _normalize_package("Peng-Robinson") == "PR"

    def test_pr(self):
        assert _normalize_package("PR") == "PR"

    def test_nrtl_lowercase(self):
        assert _normalize_package("nrtl") == "NRTL"

    def test_srk(self):
        assert _normalize_package("SRK") == "SRK"

    def test_hydrocarbons_auto_pr(self):
        assert _auto_select_package(["Hydrogen", "Methane", "Benzene", "Toluene"]) == "PR"

    def test_polar_auto_nrtl(self):
        assert _auto_select_package(["Ethanol", "Water"]) == "NRTL"


# ─────────────────────────────────────────────────────────────────────────────
#  3.  VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

class TestValidation:

    def test_professor_task_passes(self, professor_task):
        validate_flash_task(professor_task)

    def test_ethanol_water_passes(self, ethanol_water_task):
        validate_flash_task(ethanol_water_task)

    def test_three_component_passes(self, three_component_task):
        validate_flash_task(three_component_task)

    def test_missing_pressure(self, professor_task):
        del professor_task["pressure_Pa"]
        with pytest.raises(ValueError, match="missing required keys"):
            validate_flash_task(professor_task)

    def test_missing_temperature(self, professor_task):
        del professor_task["temperature_K"]
        with pytest.raises(ValueError, match="missing required keys"):
            validate_flash_task(professor_task)

    def test_composition_not_sum_to_one(self, professor_task):
        professor_task["components"]["Hydrogen"] = 0.9  # now sums to 1.5
        with pytest.raises(ValueError, match="sum to 1"):
            validate_flash_task(professor_task)

    def test_flash_pressure_above_feed(self, professor_task):
        professor_task["flash_pressure_Pa"] = 5000000.0
        with pytest.raises(ValueError, match="flash_pressure_Pa"):
            validate_flash_task(professor_task)

    def test_invalid_package(self, professor_task):
        professor_task["property_package"] = "MAGIC"
        with pytest.raises(ValueError, match="property_package"):
            validate_flash_task(professor_task)

    def test_peng_robinson_string_accepted(self, professor_task):
        """'Peng-Robinson' from professor's JSON must not raise."""
        validate_flash_task(professor_task)   # already uses "Peng-Robinson"

    def test_default_flash_pressure_is_less_than_feed(self, professor_task):
        """If flash_pressure_Pa not given, default (× 0.971) must be < feed."""
        del professor_task["flash_pressure_Pa"]
        validate_flash_task(professor_task)   # must not raise


# ─────────────────────────────────────────────────────────────────────────────
#  4.  MOCK FLASH — PROFESSOR'S EXAMPLE
# ─────────────────────────────────────────────────────────────────────────────

class TestMockFlashProfessorExample:

    def test_returns_4_streams(self, professor_task):
        res = _mock_flash(professor_task)
        for key in ("feed", "cooler_out", "vapor", "liquid"):
            assert key in res, f"Missing stream: {key}"

    def test_no_valve_out_stream(self, professor_task):
        """No valve → no 'valve_out' key in results."""
        res = _mock_flash(professor_task)
        assert "valve_out" not in res

    def test_feed_temp_preserved(self, professor_task):
        res = _mock_flash(professor_task)
        assert abs(res["feed"]["T_K"] - 410.0) < 0.1

    def test_feed_pressure_preserved(self, professor_task):
        res = _mock_flash(professor_task)
        assert abs(res["feed"]["P_Pa"] - 3447370.0) < 1.0

    def test_cooler_outlet_temperature(self, professor_task):
        res = _mock_flash(professor_task)
        assert abs(res["cooler_out"]["T_K"] - 320.0) < 0.1

    def test_cooler_outlet_pressure(self, professor_task):
        """Cooler outlet should be at flash_pressure_Pa (3350000 Pa)."""
        res = _mock_flash(professor_task)
        assert abs(res["cooler_out"]["P_Pa"] - 3350000.0) < 1.0

    def test_vapor_fraction_approx_0906(self, professor_task):
        """Validated V_frac ≈ 0.906. Allow ±0.10 for mock approximation."""
        res = _mock_flash(professor_task)
        vf = res["cooler_out"]["vapor_fraction"]
        assert 0.80 <= vf <= 1.0, f"V_frac {vf:.4f} outside expected range"

    def test_h2_enriched_in_vapor(self, professor_task):
        res = _mock_flash(professor_task)
        assert res["vapor"]["x_Hydrogen"] > res["liquid"]["x_Hydrogen"]

    def test_benzene_enriched_in_liquid(self, professor_task):
        res = _mock_flash(professor_task)
        assert res["liquid"]["x_Benzene"] > res["vapor"]["x_Benzene"]

    def test_toluene_enriched_in_liquid(self, professor_task):
        res = _mock_flash(professor_task)
        assert res["liquid"]["x_Toluene"] > res["vapor"]["x_Toluene"]

    def test_vapor_mole_fracs_sum_to_one(self, professor_task):
        res = _mock_flash(professor_task)
        total = sum(v for k, v in res["vapor"].items() if k.startswith("x_"))
        assert abs(total - 1.0) < 0.005

    def test_liquid_mole_fracs_sum_to_one(self, professor_task):
        res = _mock_flash(professor_task)
        total = sum(v for k, v in res["liquid"].items() if k.startswith("x_"))
        assert abs(total - 1.0) < 0.005

    def test_vapor_is_fully_vapor(self, professor_task):
        res = _mock_flash(professor_task)
        assert res["vapor"]["vapor_fraction"] == 1.0

    def test_liquid_is_fully_liquid(self, professor_task):
        res = _mock_flash(professor_task)
        assert res["liquid"]["vapor_fraction"] == 0.0

    def test_molar_flow_conservation(self, professor_task):
        res = _mock_flash(professor_task)
        feed = res["feed"]["molar_flow_molh"]
        v    = res["vapor"]["molar_flow_molh"]
        l    = res["liquid"]["molar_flow_molh"]
        assert abs(v + l - feed) / feed < 0.01, \
            f"Flow not conserved: V+L={v+l:.1f}, feed={feed:.1f}"

    def test_all_components_in_vapor(self, professor_task):
        res = _mock_flash(professor_task)
        for comp in professor_task["components"]:
            assert f"x_{comp}" in res["vapor"]

    def test_all_components_in_liquid(self, professor_task):
        res = _mock_flash(professor_task)
        for comp in professor_task["components"]:
            assert f"x_{comp}" in res["liquid"]

    def test_cooler_temp_c_matches_45c(self, professor_task):
        """Validated: cooler outlet = 46.85°C ≈ 320 K."""
        res = _mock_flash(professor_task)
        assert abs(res["cooler_out"]["T_C"] - 46.85) < 0.5


# ─────────────────────────────────────────────────────────────────────────────
#  5.  MOCK FLASH — OTHER SYSTEMS
# ─────────────────────────────────────────────────────────────────────────────

class TestMockFlashOtherSystems:

    def test_ethanol_water_runs(self, ethanol_water_task):
        res = _mock_flash(ethanol_water_task)
        assert "vapor" in res and "liquid" in res

    def test_three_component_all_fracs_present(self, three_component_task):
        res = _mock_flash(three_component_task)
        for comp in ["Ethanol", "Water", "Acetone"]:
            assert f"x_{comp}" in res["vapor"]
            assert f"x_{comp}" in res["liquid"]

    def test_lower_cooler_temp_less_vapor(self, professor_task):
        task_hi = dict(professor_task); task_hi["cooler_temp_K"] = 340.0
        task_lo = dict(professor_task); task_lo["cooler_temp_K"] = 300.0
        vf_hi = _mock_flash(task_hi)["cooler_out"]["vapor_fraction"]
        vf_lo = _mock_flash(task_lo)["cooler_out"]["vapor_fraction"]
        assert vf_hi >= vf_lo

    def test_default_cooler_temp_is_feed_minus_90(self):
        task = {
            "components":  {"Benzene": 0.5, "Toluene": 0.5},
            "pressure_Pa": 500000.0,
            "temperature_K": 400.0,
            "flash_pressure_Pa": 400000.0,
        }
        res = _mock_flash(task)
        # Default cooler_temp_K = 400 - 90 = 310 K
        assert abs(res["cooler_out"]["T_K"] - 310.0) < 0.5


# ─────────────────────────────────────────────────────────────────────────────
#  6.  DATAFRAME — 4 rows, correct stream names
# ─────────────────────────────────────────────────────────────────────────────

class TestDataframe:

    def test_has_4_rows(self, professor_task):
        df = results_to_dataframe(_mock_flash(professor_task))
        assert len(df) == 4

    def test_stream_names(self, professor_task):
        df = results_to_dataframe(_mock_flash(professor_task))
        assert set(df["stream"]) == {"FEED", "FFEED", "V", "L"}

    def test_no_valve_out_row(self, professor_task):
        df = results_to_dataframe(_mock_flash(professor_task))
        assert "VALVE_OUT" not in df["stream"].values

    def test_required_columns(self, professor_task):
        df = results_to_dataframe(_mock_flash(professor_task))
        for col in ("T_K", "T_C", "P_Pa", "P_bar", "molar_flow_molh", "vapor_fraction"):
            assert col in df.columns

    def test_component_columns(self, professor_task):
        df = results_to_dataframe(_mock_flash(professor_task))
        for comp in professor_task["components"]:
            assert f"x_{comp}" in df.columns

    def test_print_summary_runs(self, professor_task, capsys):
        print_flash_summary(_mock_flash(professor_task))
        out = capsys.readouterr().out
        assert "FLASH FLOWSHEET" in out
        assert "no valve" in out


# ─────────────────────────────────────────────────────────────────────────────
#  7.  FULL PIPELINE INTEGRATION (mock mode)
# ─────────────────────────────────────────────────────────────────────────────

class TestFullPipeline:

    def test_returns_dict(self, professor_task, tmp_path):
        res = run_flash_task(professor_task, output_dir=str(tmp_path))
        assert isinstance(res, dict)

    def test_csv_written(self, professor_task, tmp_path):
        run_flash_task(professor_task, output_dir=str(tmp_path))
        csv = tmp_path / "flash_Hydrogen_Methane_Benzene_Toluene.csv"
        assert csv.exists()

    def test_csv_has_4_rows(self, professor_task, tmp_path):
        run_flash_task(professor_task, output_dir=str(tmp_path))
        df = pd.read_csv(tmp_path / "flash_Hydrogen_Methane_Benzene_Toluene.csv")
        assert len(df) == 4

    def test_csv_no_valve_out(self, professor_task, tmp_path):
        run_flash_task(professor_task, output_dir=str(tmp_path))
        df = pd.read_csv(tmp_path / "flash_Hydrogen_Methane_Benzene_Toluene.csv")
        assert "VALVE_OUT" not in df["stream"].values

    def test_invalid_task_raises(self, tmp_path):
        with pytest.raises((ValueError, KeyError)):
            run_flash_task({"components": {"Methane": 1.0}}, output_dir=str(tmp_path))

    def test_ethanol_water_pipeline(self, ethanol_water_task, tmp_path):
        res = run_flash_task(ethanol_water_task, output_dir=str(tmp_path))
        assert "vapor" in res and "liquid" in res

    def test_three_component_pipeline(self, three_component_task, tmp_path):
        res = run_flash_task(three_component_task, output_dir=str(tmp_path))
        csv = tmp_path / "flash_Ethanol_Water_Acetone.csv"
        assert csv.exists()

    def test_peng_robinson_string_end_to_end(self, professor_task, tmp_path):
        """'Peng-Robinson' from LLM must work through the full pipeline."""
        res = run_flash_task(professor_task, output_dir=str(tmp_path))
        assert isinstance(res, dict)

    def test_auto_package_no_key(self, tmp_path):
        task = {
            "components":  {"Hydrogen": 0.5, "Methane": 0.5},
            "pressure_Pa": 2000000.0,
            "temperature_K": 300.0,
            "flash_pressure_Pa": 1900000.0,
        }
        res = run_flash_task(task, output_dir=str(tmp_path))
        assert "PR" in res["property_package"] or "MOCK" in res["property_package"]


# ─────────────────────────────────────────────────────────────────────────────
#  8.  EXAMPLE JSON FILES
# ─────────────────────────────────────────────────────────────────────────────

class TestExampleJsonFiles:

    def _load(self, fname):
        path = os.path.join(EXAMPLES_DIR, fname)
        if not os.path.isfile(path):
            pytest.skip(f"Not found: {fname}")
        with open(path) as f:
            return json.load(f)

    def test_professor_example_valid(self):
        task = self._load("flash_h2_ch4_benzene_toluene.json")
        validate_flash_task(task)

    def test_ethanol_water_example_valid(self):
        task = self._load("flash_ethanol_water.json")
        validate_flash_task(task)

    def test_professor_example_has_correct_format(self):
        """Verify the JSON uses dict format, not list format."""
        task = self._load("flash_h2_ch4_benzene_toluene.json")
        assert isinstance(task["components"], dict), \
            "'components' should be a dict {name: mole_fraction}"

    def test_professor_example_composition_sums_to_one(self):
        task = self._load("flash_h2_ch4_benzene_toluene.json")
        total = sum(task["components"].values())
        assert abs(total - 1.0) < 0.01


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
