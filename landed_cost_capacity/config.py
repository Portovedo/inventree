"""Access to plugin settings with typed defaults (usable from services, views and tasks)."""

from __future__ import annotations

from dataclasses import dataclass

from .allocation import CATEGORY_DUTY, CATEGORY_IMPORT_VAT, CATEGORY_OTHER, CATEGORY_SHIPPING, METHOD_VALUE

SLUG = 'landed-cost-capacity'

# Default fee classification rules: (priority, regex, category). First match wins.
DEFAULT_FEE_RULES = [
    (10, r'\bvat\b|\biva\b|import tax', CATEGORY_IMPORT_VAT),
    (20, r'customs|dut(y|ies)|tariff|alf[aâ]ndega|aduan|direitos', CATEGORY_DUTY),
    (30, r'deliver|shipping|freight|courier|carriage|postage|portes|envio|transporte|expedi', CATEGORY_SHIPPING),
    (40, r'prototype|\bnre\b|engineering|tooling', 'excluded'),
]


@dataclass
class Settings:
    auto_apply: bool = True
    auto_apply_since: str = ''
    default_method: str = METHOD_VALUE
    include_import_vat: bool = False
    capacity_exclude_external: bool = True
    capacity_include_allocated: bool = False
    capacity_include_optional: bool = False
    capacity_include_consumable: bool = False
    capacity_use_variants: bool = True
    capacity_use_substitutes: bool = True
    plan_include_pending_orders: bool = True
    uplift_lookback_days: int = 0
    cost_include_consumable: bool = True

    @property
    def included_categories(self) -> tuple[str, ...]:
        cats = [CATEGORY_SHIPPING, CATEGORY_DUTY, CATEGORY_OTHER]
        if self.include_import_vat:
            cats.append(CATEGORY_IMPORT_VAT)
        return tuple(cats)


def get_plugin():
    from plugin.registry import registry

    return registry.get_plugin(SLUG)


def _bool(value, default):
    if value is None or value == '':
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in ('1', 'true', 'yes', 'on')


def load_settings() -> Settings:
    s = Settings()
    plugin = get_plugin()
    if plugin is None:
        return s

    def get(key):
        try:
            return plugin.get_setting(key)
        except Exception:
            return None

    s.auto_apply = _bool(get('AUTO_APPLY'), s.auto_apply)
    s.auto_apply_since = get('AUTO_APPLY_SINCE') or ''
    s.default_method = get('DEFAULT_METHOD') or s.default_method
    s.include_import_vat = _bool(get('INCLUDE_IMPORT_VAT'), s.include_import_vat)
    s.capacity_exclude_external = _bool(get('CAPACITY_EXCLUDE_EXTERNAL'), s.capacity_exclude_external)
    s.capacity_include_allocated = _bool(get('CAPACITY_INCLUDE_ALLOCATED'), s.capacity_include_allocated)
    s.capacity_include_optional = _bool(get('CAPACITY_INCLUDE_OPTIONAL'), s.capacity_include_optional)
    s.capacity_include_consumable = _bool(get('CAPACITY_INCLUDE_CONSUMABLE'), s.capacity_include_consumable)
    s.capacity_use_variants = _bool(get('CAPACITY_USE_VARIANTS'), s.capacity_use_variants)
    s.capacity_use_substitutes = _bool(get('CAPACITY_USE_SUBSTITUTES'), s.capacity_use_substitutes)
    s.plan_include_pending_orders = _bool(get('PLAN_INCLUDE_PENDING_ORDERS'), s.plan_include_pending_orders)
    s.cost_include_consumable = _bool(get('COST_INCLUDE_CONSUMABLE'), s.cost_include_consumable)
    try:
        s.uplift_lookback_days = int(get('UPLIFT_LOOKBACK_DAYS') or 0)
    except (TypeError, ValueError):
        s.uplift_lookback_days = 0
    return s
