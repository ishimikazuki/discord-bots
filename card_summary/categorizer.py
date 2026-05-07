"""Merchant → category resolver. Dictionary first, LLM fallback."""
from __future__ import annotations
import logging
from pathlib import Path
from typing import Callable
from card_summary.store import get_category_for, set_category_rule
from card_summary.config import CATEGORIES

log = logging.getLogger(__name__)

LlmFn = Callable[[str], str]

class Categorizer:
    def __init__(self, db_path: Path, llm_fn: LlmFn):
        self.db_path = db_path
        self.llm_fn = llm_fn

    def categorize(self, merchant: str) -> str | None:
        if not merchant:
            return None
        # 1. dictionary lookup
        hit = get_category_for(self.db_path, merchant)
        if hit:
            return hit
        # 2. LLM fallback
        try:
            result = self.llm_fn(merchant)
        except Exception as e:
            log.warning("LLM categorize failed for %r: %s", merchant, e)
            return None
        if result not in CATEGORIES:
            log.warning("LLM returned invalid category %r for %r", result, merchant)
            return None
        # 3. learn (use upper-cased merchant as the pattern; substring match handles variants)
        set_category_rule(self.db_path, merchant.upper(), result, source="llm")
        return result
