"""Landed Cost & Capacity - InvenTree plugin.

- Spreads purchase order fees (shipping, duty, ...) over the order's own lines and writes
  the landed unit cost into received stock items (reversible per order).
- Multi-level build capacity, bottlenecks and an "N units" production/purchasing plan.
"""

import importlib
import sys

from django.utils import timezone

from plugin import InvenTreePlugin
from plugin.mixins import AppMixin, EventMixin, ScheduleMixin, SettingsMixin, UrlsMixin, UserInterfaceMixin


def _reload_submodules():
    """Hot upgrade support.

    When the plugin is upgraded from the UI, InvenTree re-imports this module but keeps the
    previously imported sibling modules in sys.modules, mixing old and new code. Reload them
    (dependencies first) so a new version works without restarting the server. Models and
    migrations are deliberately not reloaded.
    """
    package = __package__ or 'landed_cost_capacity'
    names = ['', '.allocation', '.capacity', '.config', '.landed', '.costing', '.capacity_service',
             '.dashboard_service', '.serializers', '.views']
    for name in names:
        module = sys.modules.get(package + name)
        if module is not None:
            importlib.reload(module)


_reload_submodules()

from . import PLUGIN_VERSION  # noqa: E402
from .config import SLUG  # noqa: E402

ORDER_EVENTS = ('purchaseorderitem.received', 'purchaseorder.completed')


class LandedCostCapacity(AppMixin, EventMixin, ScheduleMixin, SettingsMixin, UserInterfaceMixin, UrlsMixin, InvenTreePlugin):
    """Landed cost allocation and multi-level build capacity."""

    TITLE = 'Landed Cost & Capacity'
    NAME = 'LandedCostCapacity'
    SLUG = SLUG
    DESCRIPTION = 'Landed cost allocation of purchase order fees, multi-level build capacity and production planning'
    VERSION = PLUGIN_VERSION
    AUTHOR = 'WADA Solutions'
    WEBSITE = 'https://github.com/Portovedo/inventree'
    LICENSE = 'MIT'
    MIN_VERSION = '1.5.0'

    ADMIN_SOURCE = 'Settings.js:RenderPluginSettings'

    SCHEDULED_TASKS = {
        'nightly_check': {
            'func': 'nightly_check',
            'schedule': 'D',
        },
    }

    SETTINGS = {
        'AUTO_APPLY': {
            'name': 'Apply landed cost automatically',
            'description': 'Write landed cost into stock purchase prices when goods are received and when an order completes',
            'validator': bool,
            'default': True,
        },
        'AUTO_APPLY_SINCE': {
            'name': 'Automatic apply start date',
            'description': 'Nightly check applies orders completed after this date (YYYY-MM-DD). Set automatically to the install date; older orders are handled with the backfill preview.',
            'default': '',
        },
        'DEFAULT_METHOD': {
            'name': 'Default allocation method',
            'description': 'How fees are spread over order lines (can be overridden per order)',
            'choices': [('value', 'By line value'), ('quantity', 'By quantity'), ('manual', 'Manual weights')],
            'default': 'value',
        },
        'INCLUDE_IMPORT_VAT': {
            'name': 'Include import VAT in landed cost',
            'description': 'Enable only if import VAT is NOT recoverable for your company',
            'validator': bool,
            'default': False,
        },
        'CAPACITY_EXCLUDE_EXTERNAL': {
            'name': 'Capacity: exclude external locations',
            'description': 'Ignore stock held at external locations (e.g. a contract manufacturer)',
            'validator': bool,
            'default': True,
        },
        'CAPACITY_INCLUDE_ALLOCATED': {
            'name': 'Capacity: count allocated stock',
            'description': 'Count stock already allocated to build/sales orders as available',
            'validator': bool,
            'default': False,
        },
        'CAPACITY_INCLUDE_OPTIONAL': {
            'name': 'Capacity: include optional BOM lines',
            'validator': bool,
            'default': False,
        },
        'CAPACITY_INCLUDE_CONSUMABLE': {
            'name': 'Capacity: include consumable BOM lines',
            'validator': bool,
            'default': False,
        },
        'CAPACITY_USE_VARIANTS': {
            'name': 'Capacity: use variant stock',
            'description': 'Use stock of variant parts where the BOM line allows variants',
            'validator': bool,
            'default': True,
        },
        'CAPACITY_USE_SUBSTITUTES': {
            'name': 'Capacity: use substitute stock',
            'validator': bool,
            'default': True,
        },
        'PLAN_INCLUDE_PENDING_ORDERS': {
            'name': 'Planning: count pending/on-hold POs as on order',
            'validator': bool,
            'default': True,
        },
        'UPLIFT_LOOKBACK_DAYS': {
            'name': 'Supplier fee uplift lookback (days)',
            'description': "Orders considered when estimating a supplier's typical fee uplift (0 = all)",
            'validator': int,
            'default': 0,
        },
        'COST_INCLUDE_CONSUMABLE': {
            'name': 'Assembly cost: include consumable BOM lines',
            'validator': bool,
            'default': True,
        },
    }

    # ------------------------------------------------------------------ lifecycle

    def ensure_start_date(self):
        if not self.get_setting('AUTO_APPLY_SINCE'):
            self.set_setting('AUTO_APPLY_SINCE', timezone.now().date().isoformat())

    # ------------------------------------------------------------------ events

    def wants_process_event(self, event: str) -> bool:
        return event in ORDER_EVENTS

    def process_event(self, event: str, *args, **kwargs) -> None:
        from .landed import auto_apply_order

        self.ensure_start_date()
        order_id = kwargs.get('order_id') or kwargs.get('id')
        if order_id:
            auto_apply_order(int(order_id), reason=event)

    # ------------------------------------------------------------------ schedule

    def nightly_check(self, *args, **kwargs):
        from .landed import nightly_check

        self.ensure_start_date()
        return nightly_check()

    # ------------------------------------------------------------------ API

    def setup_urls(self):
        from django.urls import path

        from . import views

        return [
            path('order/<int:pk>/', views.OrderLandedView.as_view(), name='order'),
            path('order/<int:pk>/apply/', views.OrderApplyView.as_view(), name='order-apply'),
            path('order/<int:pk>/revert/', views.OrderRevertView.as_view(), name='order-revert'),
            path('part/<int:pk>/landed/', views.PartLandedView.as_view(), name='part-landed'),
            path('part/<int:pk>/capacity/', views.PartCapacityView.as_view(), name='part-capacity'),
            path('part/<int:pk>/plan/', views.PartPlanView.as_view(), name='part-plan'),
            path('dashboard/', views.DashboardView.as_view(), name='dashboard'),
            path('dashboard/<str:widget>/', views.DashboardWidgetView.as_view(), name='dashboard-widget'),
            path('suppliers/', views.SupplierUpliftView.as_view(), name='suppliers'),
            path('backfill/', views.BackfillView.as_view(), name='backfill'),
            path('rules/', views.FeeRuleListView.as_view(), name='rules'),
            path('rules/<int:pk>/', views.FeeRuleDetailView.as_view(), name='rule-detail'),
        ]

    # ------------------------------------------------------------------ UI

    def get_ui_panels(self, request, context: dict, **kwargs):
        from .views import has_role

        target = context.get('target_model')
        pk = context.get('target_id')
        user = request.user
        panels = []

        if target == 'purchaseorder' and pk and has_role(user, 'purchase_order', 'view'):
            panels.append({
                'key': 'landed-cost-order',
                'title': 'Landed Cost',
                'description': 'Spread order fees over the order lines',
                'icon': 'ti:truck-delivery:outline',
                'source': self.plugin_static_file('Panel.js:RenderOrderPanel'),
            })

        if target == 'part' and pk and has_role(user, 'part', 'view'):
            from part.models import Part

            part = Part.objects.filter(pk=pk).first()
            if part is not None:
                if part.assembly:
                    panels.append({
                        'key': 'landed-cost-capacity',
                        'title': 'Build Capacity',
                        'description': 'Multi-level build capacity and production plan',
                        'icon': 'ti:building-factory-2:outline',
                        'source': self.plugin_static_file('Panel.js:RenderCapacityPanel'),
                    })
                if part.assembly or part.purchaseable:
                    panels.append({
                        'key': 'landed-cost-part',
                        'title': 'Landed Cost',
                        'description': 'Landed cost history and assembly cost roll-up',
                        'icon': 'ti:coin-euro:outline',
                        'source': self.plugin_static_file('Panel.js:RenderPartCostPanel'),
                    })

        return panels

    DASHBOARD_WIDGETS = [
        ('overview', 'Operations Overview', 'Key figures: what you can build, stock value, builds, purchasing, fees', 'ti:layout-dashboard:outline', 8, 2),
        ('capacity', 'Build Capacity & Cost', 'Buildable quantity, bottleneck and landed unit cost of every assembly', 'ti:building-factory-2:outline', 5, 4),
        ('production', 'Production', 'Active build orders: progress, target dates, missing components', 'ti:tool:outline', 5, 4),
        ('purchasing', 'Purchasing Needs', 'What to buy or build for active build orders and minimum stock', 'ti:shopping-cart:outline', 6, 4),
        ('incoming', 'Incoming Orders', 'Open purchase orders and recent receipts', 'ti:truck-delivery:outline', 4, 4),
        ('inventory', 'Inventory Value', 'Stock value at landed cost, by category and top parts', 'ti:coin-euro:outline', 4, 4),
        ('fees', 'Supplier Fees', 'Shipping, duty and other fees per month and per supplier', 'ti:receipt-tax:outline', 5, 4),
    ]

    def get_ui_dashboard_items(self, request, context: dict, **kwargs):
        from .views import has_role

        if not request.user or not has_role(request.user, 'part', 'view'):
            return []
        return [
            {
                'key': f'landed-cost-capacity-{key}' if key != 'capacity' else 'landed-cost-capacity-dashboard',
                'title': title,
                'description': description,
                'icon': icon,
                'source': self.plugin_static_file(f'Dashboard.js:Render{key.title()}'),
                'options': {'width': width, 'height': height},
            }
            for key, title, description, icon, width, height in self.DASHBOARD_WIDGETS
        ]
