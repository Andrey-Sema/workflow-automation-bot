import json

import pytest

from src import agent_logic, catalog_admin, config
from src.errors import CatalogLoadError


@pytest.fixture
def fake_catalog(tmp_path, monkeypatch):
    catalog_path = tmp_path / "catalog.json"
    initial = {
        "services_list": ["Катафалк"],
        "tariffs": {"extra_point": 500, "transport_base": 1000},
        "digging_rules": {"base_price_per_person": 1600, "kopka_person_count": 4},
    }
    catalog_path.write_text(json.dumps(initial, ensure_ascii=False), encoding="utf-8")

    settings = config._settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(config, "CATALOG_DATA", dict(initial))
    monkeypatch.setattr(config, "SERVICES_LIST", initial["services_list"])
    agent_logic._load_from_catalog()
    yield catalog_path
    agent_logic.find_best_service_name.cache_clear()


def test_get_editable_tariffs(fake_catalog):
    tariffs = catalog_admin.get_editable_tariffs()
    assert tariffs["extra_point"] == 500
    assert tariffs["base_price_per_person"] == 1600


def test_update_tariff_persists_and_reloads(fake_catalog):
    catalog_admin.update_tariff("extra_point", 600)

    on_disk = json.loads(fake_catalog.read_text(encoding="utf-8"))
    assert on_disk["tariffs"]["extra_point"] == 600
    assert config.CATALOG_DATA["tariffs"]["extra_point"] == 600
    # agent_logic must see the new value without a restart
    assert agent_logic.TARIFFS["extra_point"] == 600


def test_update_tariff_rejects_unknown_key(fake_catalog):
    with pytest.raises(ValueError, match="Неизвестный"):
        catalog_admin.update_tariff("not_a_real_tariff", 1)


def test_update_tariff_without_catalog_raises_friendly_error(monkeypatch):
    monkeypatch.setattr(config, "CATALOG_DATA", {})
    with pytest.raises(CatalogLoadError):
        catalog_admin.update_tariff("extra_point", 1)
