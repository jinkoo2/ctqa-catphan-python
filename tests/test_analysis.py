from ctqa_catphan.analysis import integral_non_uniformity, relative_mtf


def test_integral_non_uniformity():
    assert abs(integral_non_uniformity([10.0, 8.0, 12.0]) - (4.0 / 20.0)) < 1e-9
    assert integral_non_uniformity([]) == 0.0


def test_relative_mtf_50():
    values = [10.0, 8.0, 6.0, 4.0, 2.0]
    norm, x50 = relative_mtf(values)
    assert abs(norm[0] - 1.0) < 1e-9
    assert 2.0 < x50 < 3.0
