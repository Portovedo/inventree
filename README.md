# Landed Cost & Capacity for InvenTree

An [InvenTree](https://inventree.org) plugin that answers two questions:

1. **What did this part really cost?** Shipping, customs and other fees on a purchase
   order are spread over *that order's* lines, and the resulting landed unit cost is
   written into the purchase price of the received stock. You can revert it per order.
2. **How many can I build, and what do I need to buy to build N?** It works out build
   capacity across every BOM level, finds the bottleneck components, and produces a
   purchasing plan with estimated landed costs.

Requires InvenTree **1.5** or newer.

## Landed cost

Add fees to a purchase order as **extra line items** (e.g. "Delivery Fee", "Customs Fee").
Each extra line gets a type:

| Type | Counted in landed cost |
|------|------------------------|
| Shipping | yes |
| Duty / customs | yes |
| Other fee | yes |
| Import VAT | only if *Include import VAT* is enabled (enable it only if VAT is not recoverable) |
| Not a product cost | no (e.g. prototype / NRE / engineering charges) |

The type is detected from the line's reference and description using **fee rules**
(regular expressions you can edit in the plugin settings). You can override it on any
single order.

Fees are spread over the order lines **by value** (default), **by quantity** (stock units) or
by **manual weights**. A fee can also be limited to specific lines. Pack sizes, line
discounts and currency conversion are taken into account. Rounding is distributed so the
allocated fees always add up to the fee total exactly.

**Applying** writes `invoice unit price + fee share` into the purchase price of every stock
item received on the order, including stock that was later split off or used in builds.
The original prices are saved, and **Revert** restores them. PO line prices are never
changed. Because InvenTree's stock pricing, stock value and BOM pricing read stock item
prices, they all show landed cost once an order is applied.

Automation:

- When goods are received, and again when an order completes, landed cost is applied
  automatically (setting *Apply landed cost automatically*).
- A nightly task re-applies orders whose lines or fees changed after they were applied, and
  catches orders completed after the install date that an event missed.
- Orders that existed before installation are **never** changed automatically. Use
  *Plugin settings → Plugin Configuration → Existing orders* to preview them and apply the
  ones you choose.

### Screens

- **Purchase order → Landed Cost**: totals, uplift %, fee types, allocation method, the
  per-line invoice / fee / landed unit price, and the Apply and Revert buttons.
- **Part → Landed Cost**: landed cost history per receipt, average landed cost of stock
  on hand, and the primary supplier's typical fee uplift. For assemblies it shows a
  multi-level **cost roll-up**, the share that is fees, and the basis used for every
  component (landed stock, last purchase, supplier estimate, missing).

## Build capacity

- **Part → Build Capacity** (assemblies):
  - **Can build now**: counts finished sub-assemblies in stock first, then works out how
    many more can be built from components, using one shared component pool. It finds the
    largest possible quantity by trying candidate quantities until it pins down the maximum.
  - **From stock on hand only**: the classic one-level figure.
  - **Blocking unit #N+1**: the components that stop you building one more unit.
  - **Direct components** table with how many units each line supports.
  - **Plan a production run**: enter N to get:
    - the builds needed at every level;
    - the stock that will be used;
    - shortages;
    - what's already on order (with PO and date);
    - what's still to buy, rounded up to the supplier pack size, priced at the right price
      break plus that supplier's typical fee uplift.

    The plan can be exported to CSV.
- **Dashboard widget "Build Capacity & Cost"**: every assembly with its capacity,
  bottleneck and landed unit cost.

Capacity respects BOM attrition, setup quantity and rounding multiples, plus variants and
substitutes where the BOM line allows them. Settings control whether to:

- exclude stock in **external** locations;
- count stock already **allocated** to builds or sales orders;
- include **optional** BOM lines;
- include **consumable** BOM lines.

## Installation

From the InvenTree admin center → Plugins → *Install plugin*, or through the API:

```
POST /api/plugins/install/
{"url": "https://github.com/<owner>/inventree-landed-cost-capacity/archive/refs/heads/main.zip",
 "packagename": "inventree-landed-cost-capacity", "confirm": true}
```

Then activate the plugin. "Enable URL integration", "Enable app integration", "Enable
interface integration", "Enable event integration" and "Enable schedule integration"
must be on in *System settings → Plugins*. Database migrations run on activation (or at the
next restart, with `INVENTREE_AUTO_UPDATE`).

## Development

```
cd frontend && npm install && npm run build   # builds into landed_cost_capacity/static (committed)
python -m pytest tests                         # pure-python tests for the allocation + capacity maths
```

The allocation and capacity algorithms live in `allocation.py` and `capacity.py`. These are
plain Python modules with no Django dependency. The Django and InvenTree integration is in
`landed.py`, `costing.py` and `capacity_service.py`.

## License

MIT
