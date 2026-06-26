"""
Unit tests for ebay_integration.utils.price_comparison.

Covers the following pure/near-pure functions:

  extract_search_keywords(item_name)
    - stop-word removal (only the configured minimal set)
    - OEM part-number stripping via the 8+ char alphanumeric regex
    - 8-word limit on the returned keywords
    - empty string and None inputs both return ""

  filter_price_outliers(prices, your_price)
    - IQR-based outlier removal with a 2.0x multiplier
    - reference-based lower (25% of your_price) and upper (400%) bounds
    - absolute floor of $5 is enforced
    - fewer than 4 prices pass through unchanged
    - fallback to IQR-only when combined bounds wipe out too many prices
    - ultimate fallback: return the original list when nothing survives

  calculate_price_stats(prices, your_price)
    - correct min / max / average / count
    - correct median for both odd and even counts
    - outlier filtering is applied before the stats are computed

  compare_item_price(ebay, item)  [eBay wrapper + DB calls are mocked]
    - returns False immediately when no keywords can be extracted
    - records "No Match" when no prices come back from the API
    - records "No Match" when the filtered average is < 30% of your price
    - records "No Match" when the filtered average is > 300% of your price
    - records "Below Market" when your price is below 90% of the average
    - records "Above Market" when your price is above 110% of the average
    - records "At Market" when your price is within ±10% of the average

frappe is already mocked at the sys.modules level by ebay_integration/tests/conftest.py,
so plain imports of the module under test work without any additional patching.
"""

import types
from unittest.mock import MagicMock, patch, call

import pytest

from ebay_integration.utils.price_comparison import (
    extract_search_keywords,
    filter_price_outliers,
    calculate_price_stats,
    compare_item_price,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_item(name="PART-001", item_name="Front Bumper Cover", standard_rate=100.0):
    """Return a SimpleNamespace that mimics a Frappe item dict with attribute access."""
    return types.SimpleNamespace(name=name, item_name=item_name, standard_rate=standard_rate)


def _ebay_results(prices, total=None):
    """Build a fake eBay search result dict from a list of float prices."""
    items = [{"price": {"value": str(p)}} for p in prices]
    return {"items": items, "total": total if total is not None else len(items)}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Suppress time.sleep inside compare_item_price so tests run instantly."""
    monkeypatch.setattr(
        "ebay_integration.utils.price_comparison.time.sleep",
        lambda _: None,
    )


@pytest.fixture
def mock_ebay():
    """Return a MagicMock that stands in for an eBayWrapper instance."""
    ebay = MagicMock(name="eBayWrapper")
    # Default: return empty results on every call
    ebay.search_similar_items.return_value = _ebay_results([])
    return ebay


# ---------------------------------------------------------------------------
# extract_search_keywords
# ---------------------------------------------------------------------------

class TestExtractSearchKeywords:

    def test_simple_title_lowercased(self):
        assert extract_search_keywords("Front Bumper Cover") == "front bumper cover"

    def test_stop_words_removed(self):
        # All configured stop words should be stripped
        result = extract_search_keywords("the a an for and or with in on at to of is it by")
        assert result == ""

    def test_stop_words_within_title(self):
        # "for" and "the" should be removed; other words should survive
        result = extract_search_keywords("Mount for the Engine")
        assert "for" not in result.split()
        assert "the" not in result.split()
        assert "mount" in result.split()
        assert "engine" in result.split()

    def test_oem_part_number_stripped(self):
        # 8+ consecutive alphanumeric chars are treated as OEM numbers and removed
        # "12345678" is 8 digits → stripped; "oem" is only 3 chars → kept
        result = extract_search_keywords("Engine Oil Drain Plug OEM# 12345678")
        assert "12345678" not in result
        assert "engine" in result

    def test_oem_number_embedded_in_title(self):
        # Two OEM numbers (8+ chars each) should both be stripped
        result = extract_search_keywords("Alternator 12345678 90123456 test")
        assert "12345678" not in result
        assert "90123456" not in result
        assert "test" in result

    def test_eight_word_limit_enforced(self):
        # 11-word title: only the first 8 meaningful words should come back
        title = "Door Mirror Left Side Power Heated Chrome Turn Signal Puddle Lamp"
        result = extract_search_keywords(title)
        assert result == "door mirror left side power heated chrome turn"
        assert len(result.split()) == 8

    def test_empty_string_returns_empty(self):
        assert extract_search_keywords("") == ""

    def test_none_returns_empty(self):
        assert extract_search_keywords(None) == ""

    def test_short_words_filtered(self):
        # Single-character words should be excluded (len <= 1)
        result = extract_search_keywords("A B C Door Panel")
        words = result.split()
        assert all(len(w) > 1 for w in words)
        assert "door" in words
        assert "panel" in words

    def test_year_range_kept(self):
        # Hyphenated year ranges are not 8+ pure alphanumeric; they survive
        result = extract_search_keywords("Oxygen Sensor Front Left Bank 1 2007-2012")
        assert "2007-2012" in result

    def test_normal_words_under_8_chars_kept(self):
        # "mount" is 5 chars — well under the 8-char OEM threshold
        result = extract_search_keywords("transmission mount 2019-2021 bracket")
        assert "mount" in result
        assert "bracket" in result

    def test_long_word_stripped_by_oem_regex(self):
        # "transmission" is 12 chars, all lowercase alphanumeric → OEM regex removes it
        result = extract_search_keywords("transmission mount bracket")
        assert "transmission" not in result
        assert "mount" in result
        assert "bracket" in result


# ---------------------------------------------------------------------------
# filter_price_outliers
# ---------------------------------------------------------------------------

class TestFilterPriceOutliers:

    def test_fewer_than_four_prices_pass_through(self):
        prices = [10.0, 20.0, 30.0]
        assert filter_price_outliers(prices) == prices

    def test_fewer_than_four_prices_with_your_price_pass_through(self):
        prices = [50.0, 60.0, 70.0]
        assert filter_price_outliers(prices, your_price=100.0) == prices

    def test_iqr_removes_high_outlier(self):
        # 6 prices; 500.0 is far beyond IQR upper bound and also above ref_upper
        prices = [100.0, 105.0, 110.0, 95.0, 98.0, 500.0]
        result = filter_price_outliers(prices, your_price=100.0)
        assert 500.0 not in result
        assert set(result) == {95.0, 98.0, 100.0, 105.0, 110.0}

    def test_reference_upper_bound_applied(self):
        # 400% cap: anything above 400 should be excluded when your_price=100
        prices = [90.0, 95.0, 100.0, 105.0, 405.0]
        result = filter_price_outliers(prices, your_price=100.0)
        assert 405.0 not in result

    def test_reference_lower_bound_fallback_to_iqr(self):
        # prices [20,21,22,23] are all below ref_lower=25 (for your_price=100).
        # The primary combined filter yields 0, so the IQR-only fallback recovers them.
        prices = [20.0, 21.0, 22.0, 23.0]
        result = filter_price_outliers(prices, your_price=100.0)
        # IQR bounds (lower=17, upper=27) cover all four prices and all are >= abs_lower
        assert result == prices

    def test_absolute_floor_enforced(self):
        # Prices below $5 should be excluded by the absolute minimum
        prices = [4.0, 50.0, 55.0, 60.0, 65.0]
        result = filter_price_outliers(prices, your_price=100.0)
        assert 4.0 not in result

    def test_all_outliers_returns_original(self):
        # All prices are below the $5 absolute floor AND below the IQR+abs fallback threshold.
        # Both filter passes fail → original list is returned.
        prices = [1.0, 2.0, 3.0, 4.0]
        result = filter_price_outliers(prices, your_price=100.0)
        assert result == prices

    def test_no_your_price_uses_default_ref_lower(self):
        # Without your_price, ref_lower defaults to 10.0
        prices = [8.0, 50.0, 55.0, 60.0, 65.0]
        result = filter_price_outliers(prices)
        assert 8.0 not in result

    def test_no_your_price_no_ref_upper_cap(self):
        # Without your_price there is no ref_upper, only IQR upper
        # 500 is a clear IQR outlier relative to the cluster
        prices = [95.0, 100.0, 105.0, 110.0, 500.0]
        result = filter_price_outliers(prices)
        assert 500.0 not in result

    def test_tight_cluster_all_survive(self):
        prices = [98.0, 99.0, 100.0, 101.0, 102.0]
        result = filter_price_outliers(prices, your_price=100.0)
        assert sorted(result) == sorted(prices)


# ---------------------------------------------------------------------------
# calculate_price_stats
# ---------------------------------------------------------------------------

class TestCalculatePriceStats:

    def test_odd_count_median(self):
        # 5 prices, no your_price so no outlier removal; median is the middle element
        prices = [10.0, 20.0, 30.0, 40.0, 50.0]
        stats = calculate_price_stats(prices)
        assert stats["median"] == 30.0
        assert stats["count"] == 5

    def test_even_count_median(self):
        # 6 prices; median = (30+40)/2 = 35
        prices = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
        stats = calculate_price_stats(prices)
        assert stats["median"] == 35.0
        assert stats["count"] == 6

    def test_min_max_correct(self):
        prices = [10.0, 20.0, 30.0, 40.0, 50.0]
        stats = calculate_price_stats(prices)
        assert stats["lowest"] == 10.0
        assert stats["highest"] == 50.0

    def test_average_correct(self):
        prices = [10.0, 20.0, 30.0, 40.0, 50.0]
        stats = calculate_price_stats(prices)
        assert stats["average"] == pytest.approx(30.0)

    def test_count_matches_filtered_length(self):
        # 5 prices in a clean cluster around your_price=100; all should survive filtering
        prices = [90.0, 95.0, 100.0, 105.0, 110.0]
        stats = calculate_price_stats(prices, your_price=100.0)
        assert stats["count"] == 5

    def test_outlier_excluded_from_stats(self):
        # 500 is a clear outlier; it should be removed before stats are calculated
        prices = [90.0, 95.0, 100.0, 105.0, 110.0, 500.0]
        stats = calculate_price_stats(prices, your_price=100.0)
        assert stats["highest"] < 200.0
        assert stats["count"] == 5

    def test_all_stats_keys_present(self):
        prices = [10.0, 20.0, 30.0, 40.0, 50.0]
        stats = calculate_price_stats(prices)
        assert set(stats.keys()) == {"lowest", "highest", "average", "median", "count"}

    def test_average_with_your_price(self):
        prices = [90.0, 95.0, 100.0, 105.0, 110.0]
        stats = calculate_price_stats(prices, your_price=100.0)
        assert stats["average"] == pytest.approx(100.0)
        assert stats["median"] == pytest.approx(100.0)

    def test_two_price_even_median(self):
        # 2 prices < 4 so filter is bypassed; even median = (10+20)/2 = 15
        prices = [10.0, 20.0]
        stats = calculate_price_stats(prices)
        assert stats["median"] == 15.0
        assert stats["average"] == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# compare_item_price
# ---------------------------------------------------------------------------

class TestCompareItemPrice:
    """
    Tests for compare_item_price(ebay, item).

    create_comparison_record is patched so we can inspect what position was
    recorded without hitting the Frappe database. time.sleep is suppressed by
    the module-level no_sleep autouse fixture.
    """

    PATCH_TARGET = "ebay_integration.utils.price_comparison.create_comparison_record"

    # -- keyword extraction --------------------------------------------------

    def test_returns_false_when_no_keywords(self, mock_ebay):
        # An item whose name reduces to nothing after stop-word + OEM stripping
        item = _make_item(item_name="the a an for and or with")
        result = compare_item_price(mock_ebay, item)
        assert result is False
        mock_ebay.search_similar_items.assert_not_called()

    # -- no prices found -----------------------------------------------------

    def test_no_match_when_no_prices_returned(self, mock_ebay):
        item = _make_item(item_name="Front Bumper Cover", standard_rate=100.0)
        mock_ebay.search_similar_items.return_value = _ebay_results([])

        with patch(self.PATCH_TARGET) as mock_record:
            result = compare_item_price(mock_ebay, item)

        assert result is True
        # All three passes should have fired before giving up
        assert mock_ebay.search_similar_items.call_count == 3
        mock_record.assert_called_once()
        _item_arg, _kw_arg, stats_arg, position_arg, _total_arg = mock_record.call_args.args
        assert position_arg == "No Match"
        assert stats_arg["average"] == 0

    # -- sanity check: average too low (wrong items matched) -----------------

    def test_no_match_when_average_below_30_percent_of_your_price(self, mock_ebay):
        # your_price=100; prices average to 27 after filtering → 27 < 30
        item = _make_item(item_name="Front Bumper Cover", standard_rate=100.0)
        low_prices = [25.0, 26.0, 27.0, 28.0, 29.0]
        mock_ebay.search_similar_items.return_value = _ebay_results(low_prices, total=5)

        with patch(self.PATCH_TARGET) as mock_record:
            result = compare_item_price(mock_ebay, item)

        assert result is True
        _item_arg, _kw_arg, stats_arg, position_arg, _total_arg = mock_record.call_args.args
        assert position_arg == "No Match"
        assert stats_arg["average"] == 0

    # -- sanity check: average too high (wrong items matched) ----------------

    def test_no_match_when_average_above_300_percent_of_your_price(self, mock_ebay):
        # your_price=100; prices average to 370 → 370 > 300
        item = _make_item(item_name="Front Bumper Cover", standard_rate=100.0)
        high_prices = [350.0, 360.0, 370.0, 380.0, 390.0]
        mock_ebay.search_similar_items.return_value = _ebay_results(high_prices, total=5)

        with patch(self.PATCH_TARGET) as mock_record:
            result = compare_item_price(mock_ebay, item)

        assert result is True
        _item_arg, _kw_arg, stats_arg, position_arg, _total_arg = mock_record.call_args.args
        assert position_arg == "No Match"
        assert stats_arg["average"] == 0

    # -- price position: below market ----------------------------------------

    def test_records_below_market_when_price_below_90_percent_of_average(self, mock_ebay):
        # your_price=100; eBay average=150 → 100 < 150*0.9=135  → Below Market
        item = _make_item(item_name="Front Bumper Cover", standard_rate=100.0)
        prices = [140.0, 150.0, 155.0, 145.0, 160.0]
        mock_ebay.search_similar_items.return_value = _ebay_results(prices, total=5)

        with patch(self.PATCH_TARGET) as mock_record:
            result = compare_item_price(mock_ebay, item)

        assert result is True
        _item_arg, _kw_arg, stats_arg, position_arg, _total_arg = mock_record.call_args.args
        assert position_arg == "Below Market"
        assert stats_arg["average"] == pytest.approx(150.0)

    # -- price position: above market ----------------------------------------

    def test_records_above_market_when_price_above_110_percent_of_average(self, mock_ebay):
        # your_price=100; eBay average=60 → 100 > 60*1.1=66  → Above Market
        item = _make_item(item_name="Front Bumper Cover", standard_rate=100.0)
        prices = [55.0, 58.0, 60.0, 62.0, 65.0]
        mock_ebay.search_similar_items.return_value = _ebay_results(prices, total=5)

        with patch(self.PATCH_TARGET) as mock_record:
            result = compare_item_price(mock_ebay, item)

        assert result is True
        _item_arg, _kw_arg, stats_arg, position_arg, _total_arg = mock_record.call_args.args
        assert position_arg == "Above Market"
        assert stats_arg["average"] == pytest.approx(60.0)

    # -- price position: at market -------------------------------------------

    def test_records_at_market_when_price_within_10_percent_of_average(self, mock_ebay):
        # your_price=100; eBay average=100 → within ±10%  → At Market
        item = _make_item(item_name="Front Bumper Cover", standard_rate=100.0)
        prices = [95.0, 98.0, 100.0, 102.0, 105.0]
        mock_ebay.search_similar_items.return_value = _ebay_results(prices, total=5)

        with patch(self.PATCH_TARGET) as mock_record:
            result = compare_item_price(mock_ebay, item)

        assert result is True
        _item_arg, _kw_arg, stats_arg, position_arg, _total_arg = mock_record.call_args.args
        assert position_arg == "At Market"
        assert stats_arg["average"] == pytest.approx(100.0)

    # -- boundary: exactly at 90% threshold (not below market) ---------------

    def test_at_market_at_exact_90_percent_lower_boundary(self, mock_ebay):
        # your_price == average * 0.9 exactly → NOT < 0.9, so At Market
        # average=100 → boundary = 90; set your_price=90
        item = _make_item(item_name="Front Bumper Cover", standard_rate=90.0)
        prices = [95.0, 98.0, 100.0, 102.0, 105.0]
        mock_ebay.search_similar_items.return_value = _ebay_results(prices, total=5)

        with patch(self.PATCH_TARGET) as mock_record:
            compare_item_price(mock_ebay, item)

        _item_arg, _kw_arg, _stats_arg, position_arg, _total_arg = mock_record.call_args.args
        assert position_arg == "At Market"

    # -- boundary: exactly at 110% threshold (not above market) --------------

    def test_at_market_at_exact_110_percent_upper_boundary(self, mock_ebay):
        # your_price == average * 1.1 exactly → NOT > 1.1, so At Market
        # average=100 → boundary = 110; set your_price=110
        item = _make_item(item_name="Front Bumper Cover", standard_rate=110.0)
        prices = [95.0, 98.0, 100.0, 102.0, 105.0]
        mock_ebay.search_similar_items.return_value = _ebay_results(prices, total=5)

        with patch(self.PATCH_TARGET) as mock_record:
            compare_item_price(mock_ebay, item)

        _item_arg, _kw_arg, _stats_arg, position_arg, _total_arg = mock_record.call_args.args
        assert position_arg == "At Market"

    # -- correct item and keywords passed to record --------------------------

    def test_item_and_keywords_passed_to_record(self, mock_ebay):
        item = _make_item(name="PART-123", item_name="Air Intake Filter", standard_rate=50.0)
        prices = [48.0, 50.0, 52.0, 49.0, 51.0]
        mock_ebay.search_similar_items.return_value = _ebay_results(prices, total=5)

        with patch(self.PATCH_TARGET) as mock_record:
            compare_item_price(mock_ebay, item)

        item_arg, kw_arg, _stats_arg, _pos_arg, _total_arg = mock_record.call_args.args
        assert item_arg is item
        assert kw_arg == "air intake filter"

    # -- multi-pass fallback: second pass triggered --------------------------

    def test_second_pass_triggered_when_first_has_fewer_than_5_prices(self, mock_ebay):
        """
        Pass 1 returns only 4 prices → pass 2 is triggered.
        Pass 2 returns 5 good prices that put the item At Market.
        """
        item = _make_item(item_name="Door Panel Left", standard_rate=100.0)
        few_prices = [95.0, 98.0, 100.0, 102.0]   # 4 items → triggers pass 2
        good_prices = [95.0, 98.0, 100.0, 102.0, 105.0]  # 5 items

        mock_ebay.search_similar_items.side_effect = [
            _ebay_results(few_prices, total=4),   # pass 1
            _ebay_results(good_prices, total=5),  # pass 2
        ]

        with patch(self.PATCH_TARGET) as mock_record:
            result = compare_item_price(mock_ebay, item)

        assert result is True
        assert mock_ebay.search_similar_items.call_count == 2
        _item_arg, _kw_arg, _stats_arg, position_arg, _total_arg = mock_record.call_args.args
        assert position_arg == "At Market"

    # -- multi-pass fallback: third pass triggered ---------------------------

    def test_third_pass_triggered_when_second_pass_still_fewer_than_3(self, mock_ebay):
        """
        Pass 1 → 2 prices, pass 2 → 2 prices, pass 3 → 5 good prices.
        """
        item = _make_item(item_name="Door Panel Left", standard_rate=100.0)
        tiny = [95.0, 100.0]
        good_prices = [95.0, 98.0, 100.0, 102.0, 105.0]

        mock_ebay.search_similar_items.side_effect = [
            _ebay_results(tiny, total=2),         # pass 1
            _ebay_results(tiny, total=2),         # pass 2
            _ebay_results(good_prices, total=5),  # pass 3
        ]

        with patch(self.PATCH_TARGET) as mock_record:
            result = compare_item_price(mock_ebay, item)

        assert result is True
        assert mock_ebay.search_similar_items.call_count == 3
        _item_arg, _kw_arg, _stats_arg, position_arg, _total_arg = mock_record.call_args.args
        assert position_arg == "At Market"
