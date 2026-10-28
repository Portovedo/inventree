"""API views for the Landed Cost & Capacity plugin (mounted under /plugin/landed-cost-capacity/)."""

from decimal import Decimal, InvalidOperation

from django.shortcuts import get_object_or_404
from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import capacity_service, costing, dashboard_service, landed
from .allocation import CATEGORIES, METHODS
from .serializers import ExtraLineConfigSerializer, FeeRuleSerializer, OrderConfigSerializer


def has_role(user, role, perm):
    from users.permissions import check_user_role

    return bool(user and (user.is_superuser or check_user_role(user, role, perm)))


class RolePermission(permissions.BasePermission):
    """Require an InvenTree role: views declare ``role`` and ``read_perm`` / ``write_perm``."""

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        perm = getattr(view, 'read_perm', 'view') if request.method in permissions.SAFE_METHODS else getattr(view, 'write_perm', 'change')
        return has_role(request.user, view.role, perm)


class StaffPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)


class BaseView(APIView):
    permission_classes = [RolePermission]
    role = 'purchase_order'
    serializer_class = serializers.Serializer  # schema generation only


class OrderLandedView(BaseView):
    """GET: landed cost breakdown. POST: update method / weights / extra line overrides."""

    def get_order(self, pk):
        from order.models import PurchaseOrder

        return get_object_or_404(PurchaseOrder, pk=pk)

    def get(self, request, pk):
        order = self.get_order(pk)
        data = landed.public(landed.compute(order))
        data['can_edit'] = has_role(request.user, 'purchase_order', 'change')
        data['methods'] = list(METHODS)
        data['categories'] = list(CATEGORIES)
        return Response(data)

    def post(self, request, pk):
        from order.models import PurchaseOrderExtraLine

        from .models import ExtraLineConfig

        order = self.get_order(pk)
        cfg = landed.get_config(order, create=True)
        cfg_ser = OrderConfigSerializer(cfg, data=request.data, partial=True)
        cfg_ser.is_valid(raise_exception=True)
        cfg_ser.save()

        for entry in request.data.get('extra_lines', []) or []:
            extra = get_object_or_404(PurchaseOrderExtraLine, pk=entry.get('id'), order=order)
            obj, _ = ExtraLineConfig.objects.get_or_create(extra_line=extra)
            ser = ExtraLineConfigSerializer(obj, data=entry, partial=True)
            ser.is_valid(raise_exception=True)
            ser.save()

        data = landed.public(landed.compute(order))
        data['can_edit'] = True
        data['methods'] = list(METHODS)
        data['categories'] = list(CATEGORIES)
        return Response(data)


class OrderApplyView(OrderLandedView):
    def get(self, request, pk):
        return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def post(self, request, pk):
        order = self.get_order(pk)
        data = landed.public(landed.apply(order, request.user))
        data['can_edit'] = True
        data['methods'] = list(METHODS)
        data['categories'] = list(CATEGORIES)
        return Response(data)


class OrderRevertView(OrderLandedView):
    def get(self, request, pk):
        return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def post(self, request, pk):
        order = self.get_order(pk)
        restored = landed.revert(order)
        data = landed.public(landed.compute(order))
        data.update(can_edit=True, methods=list(METHODS), categories=list(CATEGORIES), restored=restored)
        return Response(data)


class PartView(BaseView):
    role = 'part'

    def get_part(self, pk):
        from part.models import Part

        return get_object_or_404(Part, pk=pk)


class PartLandedView(PartView):
    def get(self, request, pk):
        return Response(costing.part_landed_summary(self.get_part(pk)))


class PartCapacityView(PartView):
    def get(self, request, pk):
        return Response(capacity_service.part_capacity(self.get_part(pk)))


class PartPlanView(PartView):
    def get(self, request, pk):
        try:
            quantity = Decimal(request.query_params.get('quantity', '1'))
        except InvalidOperation:
            return Response({'quantity': 'Invalid quantity'}, status=400)
        if quantity <= 0 or quantity > 1_000_000:
            return Response({'quantity': 'Quantity must be between 0 and 1,000,000'}, status=400)
        return Response(capacity_service.plan(self.get_part(pk), quantity))


class DashboardView(BaseView):
    role = 'part'

    def get(self, request):
        refresh = request.query_params.get('refresh') == '1'
        return Response(dashboard_service.cached('capacity', capacity_service.dashboard, refresh))


class DashboardWidgetView(BaseView):
    """Data for one dashboard widget: overview, production, purchasing, incoming, inventory, fees."""

    role = 'part'
    WIDGETS = {
        'production': dashboard_service.production,
        'purchasing': dashboard_service.purchasing_needs,
        'incoming': dashboard_service.incoming,
        'inventory': dashboard_service.inventory_value,
        'fees': dashboard_service.supplier_fees,
    }

    def get(self, request, widget):
        refresh = request.query_params.get('refresh') == '1'
        if widget == 'overview':
            return Response(dashboard_service.overview(refresh=refresh))
        fn = self.WIDGETS.get(widget)
        if fn is None:
            return Response({'detail': 'Unknown widget'}, status=404)
        return Response(dashboard_service.cached(widget, fn, refresh))


class SupplierUpliftView(BaseView):
    def get(self, request):
        cache = costing.OrderCache(landed.load_settings())
        return Response({'currency': landed.default_currency(), 'suppliers': costing.supplier_uplifts(cache)})


class BackfillView(APIView):
    """GET: preview landed cost for all received orders. POST: apply (optionally {orders: [ids]})."""

    permission_classes = [StaffPermission]
    serializer_class = serializers.Serializer

    def get(self, request):
        return Response({'currency_note': 'Amounts in each order currency', 'orders': landed.backfill(dry_run=True)})

    def post(self, request):
        ids = request.data.get('orders') or None
        return Response({'orders': landed.backfill(dry_run=False, user=request.user, order_ids=ids)})


class FeeRuleListView(APIView):
    permission_classes = [StaffPermission]
    serializer_class = FeeRuleSerializer

    def get(self, request):
        from .models import FeeRule

        return Response({'categories': list(CATEGORIES), 'rules': FeeRuleSerializer(FeeRule.objects.all(), many=True).data})

    def post(self, request):
        ser = FeeRuleSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data, status=201)


class FeeRuleDetailView(APIView):
    permission_classes = [StaffPermission]
    serializer_class = FeeRuleSerializer

    def get_rule(self, pk):
        from .models import FeeRule

        return get_object_or_404(FeeRule, pk=pk)

    def patch(self, request, pk):
        ser = FeeRuleSerializer(self.get_rule(pk), data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)

    def delete(self, request, pk):
        self.get_rule(pk).delete()
        return Response(status=204)
