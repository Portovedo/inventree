"""Database models for the Landed Cost & Capacity plugin."""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from .allocation import CATEGORIES, METHODS

CATEGORY_CHOICES = [(c, c.replace('_', ' ').title()) for c in CATEGORIES]
METHOD_CHOICES = [(m, m.title()) for m in METHODS]


class FeeRule(models.Model):
    """Maps PO extra-line references/descriptions to a fee category (regex, first match wins)."""

    class Meta:
        app_label = 'landed_cost_capacity'
        ordering = ['priority', 'pk']
        verbose_name = _('Fee rule')
        verbose_name_plural = _('Fee rules')

    priority = models.PositiveIntegerField(default=100)
    pattern = models.CharField(max_length=250, help_text=_('Case-insensitive regular expression'))
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f'{self.pattern} -> {self.category}'


class OrderConfig(models.Model):
    """Per purchase order landed cost configuration and applied state."""

    class Meta:
        app_label = 'landed_cost_capacity'
        verbose_name = _('Order landed cost')
        verbose_name_plural = _('Order landed costs')

    order = models.OneToOneField(
        'order.PurchaseOrder', on_delete=models.CASCADE, related_name='landed_cost_config'
    )
    method = models.CharField(
        max_length=20, choices=METHOD_CHOICES, blank=True, default='', help_text=_('Blank = plugin default')
    )
    weights = models.JSONField(default=dict, blank=True, help_text=_('Manual weights {line_id: weight}'))

    applied = models.BooleanField(default=False)
    applied_hash = models.CharField(max_length=64, blank=True, default='')
    applied_at = models.DateTimeField(null=True, blank=True)
    applied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )
    applied_summary = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f'Landed cost config for {self.order}'


class ExtraLineConfig(models.Model):
    """User override for a single PO extra line (category and target lines)."""

    class Meta:
        app_label = 'landed_cost_capacity'

    extra_line = models.OneToOneField(
        'order.PurchaseOrderExtraLine', on_delete=models.CASCADE, related_name='landed_cost_config'
    )
    category = models.CharField(
        max_length=20, choices=CATEGORY_CHOICES, blank=True, default='', help_text=_('Blank = detect from rules')
    )
    targets = models.JSONField(default=list, blank=True, help_text=_('Goods line ids; empty = all lines'))


class StockPriceBackup(models.Model):
    """Original purchase price of a stock item before landed cost was written."""

    class Meta:
        app_label = 'landed_cost_capacity'

    stock_item = models.OneToOneField(
        'stock.StockItem', on_delete=models.CASCADE, related_name='landed_cost_backup'
    )
    order = models.ForeignKey(
        'order.PurchaseOrder', on_delete=models.CASCADE, related_name='landed_cost_backups'
    )
    original_price = models.DecimalField(max_digits=19, decimal_places=6, null=True, blank=True)
    original_currency = models.CharField(max_length=3, blank=True, default='')
    landed_price = models.DecimalField(max_digits=19, decimal_places=6, null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)
