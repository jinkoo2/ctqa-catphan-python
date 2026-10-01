from ctqa_catphan.app_settings import strip_jsonc, machine_by_station


def test_strip_jsonc():
    text = '{ "a": 1, // comment\n "b": "http://x" }'
    assert '"a"' in strip_jsonc(text)
    data = __import__("json").loads(strip_jsonc(text))
    assert data["a"] == 1
    assert data["b"] == "http://x"


def test_machine_by_station():
    data = {
        "MACHINES": [
            {"NAME": "CTSim1", "station_names": ["ctsim", "daily qa_ctsim"]}
        ]
    }
    m = machine_by_station("CTSIM", data)
    assert m["NAME"] == "CTSim1"
    assert machine_by_station("unknown", data) is None
