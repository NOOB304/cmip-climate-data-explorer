from cmip_explorer.ui.variable_labels import friendly_variable_name


def test_catalog_variables_receive_readable_chinese_labels() -> None:
    assert friendly_variable_name(
        "swsffluxaero", "Shortwave Heating Rate Due to Volcanic Aerosols"
    ) == ("火山气溶胶短波加热率")
    assert friendly_variable_name("rainmxrat27", "Mass Fraction of Rain in Air") == (
        "空气中雨水质量分数"
    )
    assert friendly_variable_name("ta27", "Air Temperature") == "空气温度"


def test_chemical_formula_is_preserved_and_unknown_new_value_stays_english() -> None:
    assert friendly_variable_name("futureCo2", "Mole Fraction of CO2") == "CO2 浓度"
    assert friendly_variable_name("brandNew", "Brand New API Variable") == (
        "Brand New API Variable"
    )
