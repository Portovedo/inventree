"""Seed default fee classification rules."""

from django.db import migrations

from landed_cost_capacity.config import DEFAULT_FEE_RULES


def seed(apps, schema_editor):
    FeeRule = apps.get_model('landed_cost_capacity', 'FeeRule')
    if FeeRule.objects.exists():
        return
    for priority, pattern, category in DEFAULT_FEE_RULES:
        FeeRule.objects.create(priority=priority, pattern=pattern, category=category, active=True)


def unseed(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [('landed_cost_capacity', '0001_initial')]

    operations = [migrations.RunPython(seed, unseed)]
