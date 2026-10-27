"""Admin site configuration for the Landed Cost & Capacity plugin."""

from django.contrib import admin

from .models import ExtraLineConfig, FeeRule, OrderConfig, StockPriceBackup


@admin.register(FeeRule)
class FeeRuleAdmin(admin.ModelAdmin):
    list_display = ('priority', 'pattern', 'category', 'active')
    list_editable = ('pattern', 'category', 'active')
    list_display_links = ('priority',)


@admin.register(OrderConfig)
class OrderConfigAdmin(admin.ModelAdmin):
    list_display = ('order', 'method', 'applied', 'applied_at', 'applied_by')
    list_filter = ('applied',)


@admin.register(ExtraLineConfig)
class ExtraLineConfigAdmin(admin.ModelAdmin):
    list_display = ('extra_line', 'category', 'targets')


@admin.register(StockPriceBackup)
class StockPriceBackupAdmin(admin.ModelAdmin):
    list_display = ('stock_item', 'order', 'original_price', 'original_currency', 'landed_price', 'created')
    list_filter = ('order',)
