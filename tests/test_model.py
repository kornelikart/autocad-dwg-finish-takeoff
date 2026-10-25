from finishcalc.model import Profile

def test_profile_json_roundtrip():
    p = Profile("_AREA", "номерок", "ААА", "тип пола", "ТИП", "_WinDoo", "_обозначения дверей", None, 1.0)
    assert Profile.from_json(p.to_json()) == p
