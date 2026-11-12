"""The user-style benchmark requests (benchmarks/user_requests) stay valid as the game data changes."""
import importlib.util

from builder import ROOT


def test_every_user_benchmark_request_compiles():
    spec = importlib.util.spec_from_file_location("gate_user", ROOT / "benchmarks" / "gate_user.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    found = dict(module.load_requests([]))  # raises SystemExit on any request the compiler rejects
    assert set(found) == {"et_acolyte_shaman", "ice_shot_gemling", "little_minions_varashta"}
    assert all(r.seed_code is None and r.max_buttons for r in found.values())  # requests as the user writes them
