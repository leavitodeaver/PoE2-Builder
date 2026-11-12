"""The user-style benchmark requests (benchmarks/user_requests) stay valid as the game data changes."""
import importlib.util

from builder import ROOT


def test_every_user_benchmark_request_compiles():
    spec = importlib.util.spec_from_file_location("gate_user", ROOT / "benchmarks" / "gate_user.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    found = dict(module.load_requests([]))  # raises SystemExit on any request the compiler rejects
    # Little Minions waits for the djinn Command model (its main skill, Kelari's Deception, is a minion skill in PoB)
    assert set(found) == {"et_acolyte_shaman", "ice_shot_gemling"}
    assert all(r.seed_code is None and r.max_buttons for r in found.values())  # requests as the user writes them
    assert all(r.budget.divines == 150 for r in found.values())  # the user, 12 Nov: "around 100-200div"
    assert found["et_acolyte_shaman"].floors.life_es == 5000 and found["et_acolyte_shaman"].max_buttons == 3


def test_a_waiting_request_still_validates():
    import json

    body = json.loads((ROOT / "benchmarks" / "user_requests" / "little_minions_varashta.json").read_text(encoding="utf-8"))
    assert body.pop("waits_for")
    from builder.request.schema import BuildRequest

    request = BuildRequest.model_validate(body)
    assert request.main_skill == "Kelari's Deception" and request.max_buttons == 2
