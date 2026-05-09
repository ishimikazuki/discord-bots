from unittest.mock import Mock
from card_summary.store import init_db, seed_category_rules, get_category_for
from card_summary.categorizer import Categorizer

def test_dict_hit_returns_category_without_calling_llm(tmp_db):
    init_db(tmp_db)
    seed_category_rules(tmp_db, {"AMAZON": "ネット通販"})
    llm = Mock(side_effect=AssertionError("LLM must not be called for dict hit"))
    cat = Categorizer(tmp_db, llm_fn=llm)
    assert cat.categorize("Amazon.co.jp") == "ネット通販"
    llm.assert_not_called()

def test_dict_hit_normalizes_epos_full_width_merchant(tmp_db):
    init_db(tmp_db)
    seed_category_rules(tmp_db, {"AP/サミツト": "食費", "GOOGLE*CLOUD": "サブスク"})
    llm = Mock(side_effect=AssertionError("LLM must not be called for normalized hit"))
    cat = Categorizer(tmp_db, llm_fn=llm)
    assert cat.categorize("ＡＰ／サミツト") == "食費"
    assert cat.categorize("ＧＯＯＧＬＥ＊ＣＬＯＵＤ ６ＺＰＰＣ６") == "サブスク"
    llm.assert_not_called()

def test_dict_miss_calls_llm_and_caches(tmp_db):
    init_db(tmp_db)
    seed_category_rules(tmp_db, {})  # empty dict
    llm = Mock(return_value="食費")
    cat = Categorizer(tmp_db, llm_fn=llm)
    result = cat.categorize("ZENIYA RAMEN")
    assert result == "食費"
    llm.assert_called_once_with("ZENIYA RAMEN")
    # Subsequent call should hit the learned rule, not LLM
    llm.reset_mock()
    assert cat.categorize("Zeniya Ramen 渋谷店") == "食費"
    llm.assert_not_called()

def test_llm_failure_returns_none(tmp_db):
    init_db(tmp_db)
    llm = Mock(side_effect=RuntimeError("LLM down"))
    cat = Categorizer(tmp_db, llm_fn=llm)
    assert cat.categorize("UNKNOWN MERCHANT") is None
