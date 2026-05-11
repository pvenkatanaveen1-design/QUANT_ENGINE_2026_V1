"""Sanity checks for sidebar navigation coverage."""

from dashboard.nav_sections import PAGE_GROUPS, PAGE_SETUP_HINT, all_page_ids_in_order


def test_all_pages_grouped_exactly_once():
    flat = all_page_ids_in_order()
    assert len(flat) == 18
    assert len(set(flat)) == 18


def test_every_page_has_setup_hint():
    for pid in all_page_ids_in_order():
        assert pid in PAGE_SETUP_HINT
        assert len(PAGE_SETUP_HINT[pid].strip()) > 10


def test_section_keys_stable():
    assert "Research" in PAGE_GROUPS
    assert "Platform & VPS" in PAGE_GROUPS
