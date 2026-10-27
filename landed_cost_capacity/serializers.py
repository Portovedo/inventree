"""API serializers for the Landed Cost & Capacity plugin."""

import re
from decimal import Decimal, InvalidOperation

from rest_framework import serializers

from .models import ExtraLineConfig, FeeRule, OrderConfig


class FeeRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = FeeRule
        fields = ['pk', 'priority', 'pattern', 'category', 'active']

    def validate_pattern(self, value):
        try:
            re.compile(value, re.IGNORECASE)
        except re.error as exc:
            raise serializers.ValidationError(f'Invalid regular expression: {exc}')
        return value


class OrderConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderConfig
        fields = ['method', 'weights']

    def validate_weights(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError('Weights must be an object {line_id: weight}')
        clean = {}
        for key, weight in value.items():
            if weight in (None, ''):
                continue
            try:
                w = Decimal(str(weight))
            except InvalidOperation:
                raise serializers.ValidationError(f'Invalid weight for line {key}')
            if w < 0:
                raise serializers.ValidationError('Weights cannot be negative')
            clean[str(int(key))] = str(w)
        return clean


class ExtraLineConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExtraLineConfig
        fields = ['category', 'targets']

    def validate_targets(self, value):
        if not isinstance(value, list) or not all(isinstance(v, int) for v in value):
            raise serializers.ValidationError('Targets must be a list of line ids')
        return value
