#!/usr/bin/env python3
"""Tests for the offline reverse geocoder backing the `test` command."""
import modules.geocoder as geocoder


def test_dataset_loads():
    assert geocoder.dataset_available(), "bundled us_cities.tsv.gz should load"


def test_bot_location_resolves_to_fairlawn_va():
    # SWVAMESH-BOT's configured coordinates (Fairlawn, VA).
    assert geocoder.nearest_city(37.1465, -80.5722) == "Fairlawn, VA"


def test_known_cities_resolve_with_state():
    assert geocoder.nearest_city(37.2710, -79.9414) == "Roanoke, VA"
    # Manhattan, NYC
    city = geocoder.nearest_city(40.7128, -74.0060)
    assert city is not None and city.endswith(", NY")


def test_invalid_and_out_of_range_coords_return_none():
    assert geocoder.nearest_city(None, -80.0) is None
    assert geocoder.nearest_city(91.0, 0.0) is None
    assert geocoder.nearest_city(0.0, 200.0) is None
    assert geocoder.nearest_city("nan", "nan") is None


def test_max_km_filters_far_matches():
    # Middle of the North Atlantic — nearest US city is far; max_km rejects it.
    assert geocoder.nearest_city(35.0, -40.0, max_km=100.0) is None
    # Without the cap, it still returns *some* nearest city (not None).
    assert geocoder.nearest_city(35.0, -40.0) is not None


def test_format_is_city_comma_state():
    city = geocoder.nearest_city(37.1465, -80.5722)
    assert city is not None
    name, _, state = city.partition(", ")
    assert name and len(state) == 2 and state.isalpha() and state.isupper()
