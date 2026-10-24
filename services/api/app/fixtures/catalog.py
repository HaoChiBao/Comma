"""Deterministic Acme Computing desktop BOM and supplier catalog.

Prices are the inputs. Tests compute expected landed cost from these values.
"""

from app.money import money

DEMO_REQUEST = (
    "Build 100 complete desktop computers for our production run, "
    "within a $200,000 total component procurement budget, delivered by November 15."
)

COMPUTER_QUANTITY = 100
BUDGET = money("200000.00")
DEADLINE_MONTH = 11
DEADLINE_DAY = 15

# Cross-component rules. The optimizer interprets these; it does not invent them.
RULES = [
    {
        "rule_type": "equals",
        "constraint": {
            "left": "cpu.socket",
            "right": "motherboard.socket",
            "message": "cpu/motherboard socket mismatch",
        },
    },
    {
        "rule_type": "equals",
        "constraint": {
            "left": "ram.memory",
            "right": "motherboard.memory",
            "message": "ram/motherboard memory type mismatch",
        },
    },
    {
        "rule_type": "member",
        "constraint": {
            "value": "cpu.socket",
            "choices": "cooling.sockets",
            "message": "cooling/cpu socket mismatch",
        },
    },
    {
        "rule_type": "wattage",
        "constraint": {
            "sum": ["gpu.tdp_w", "cpu.tdp_w"],
            "headroom": 200,
            "minimum": "psu.wattage",
            "message": "psu wattage below required headroom",
        },
    },
    {
        "rule_type": "member",
        "constraint": {
            "value": "motherboard.form_factor",
            "choices": "case.form_factors",
            "message": "case/motherboard form factor mismatch",
        },
    },
]

BOM = [
    {
        "component_type": "gpu",
        "required_sku": "ACME-GPU-7600",
        "units_per_computer": 1,
        "allowed": ["ACME-GPU-7600"],
        "spec": {"tdp_w": 180},
    },
    {
        "component_type": "cpu",
        "required_sku": "ACME-CPU-7600X",
        "units_per_computer": 1,
        "allowed": ["ACME-CPU-7600X", "ACME-CPU-LGA"],
        "spec": {"socket": "AM5", "tdp_w": 105},
    },
    {
        "component_type": "motherboard",
        "required_sku": "ACME-MB-B650",
        "units_per_computer": 1,
        "allowed": ["ACME-MB-B650", "ACME-MB-H610", "ACME-MB-B650H"],
        "spec": {"socket": "AM5", "memory": "DDR5", "form_factor": "ATX"},
    },
    {
        "component_type": "ram",
        "required_sku": "ACME-RAM-DDR5-16",
        "units_per_computer": 2,
        "allowed": ["ACME-RAM-DDR5-16"],
        "spec": {"memory": "DDR5"},
    },
    {
        "component_type": "storage",
        "required_sku": "ACME-SSD-1TB",
        "units_per_computer": 1,
        "allowed": ["ACME-SSD-1TB"],
        "spec": {},
    },
    {
        "component_type": "psu",
        "required_sku": "ACME-PSU-750",
        "units_per_computer": 1,
        "allowed": ["ACME-PSU-750", "ACME-PSU-450"],
        "spec": {"wattage": 750},
    },
    {
        "component_type": "case",
        "required_sku": "ACME-CASE-ATX",
        "units_per_computer": 1,
        "allowed": ["ACME-CASE-ATX", "ACME-CASE-MATX"],
        "spec": {"form_factors": ["ATX", "mATX"]},
    },
    {
        "component_type": "cooling",
        "required_sku": "ACME-COOL-AM5",
        "units_per_computer": 1,
        "allowed": ["ACME-COOL-AM5", "ACME-COOL-LGA"],
        "spec": {"sockets": ["AM5"]},
    },
]

SUPPLIERS = [
    {
        "code": "northwind",
        "name": "Northwind Components",
        "email": "quotes@northwind.sandbox",
        "lines": [
            {"component": "gpu", "sku": "ACME-GPU-7600", "price": "389.00", "qty": 160, "moq": 1, "lead": 10, "shipping": "75.00", "spec": {"tdp_w": 180}},
            {"component": "cpu", "sku": "ACME-CPU-7600X", "price": "214.00", "qty": 160, "moq": 1, "lead": 10, "shipping": "75.00", "spec": {"socket": "AM5", "tdp_w": 105}},
        ],
    },
    {
        "code": "helios",
        "name": "Helios Silicon",
        "email": "sales@helios.sandbox",
        "lines": [
            {"component": "gpu", "sku": "ACME-GPU-7600", "price": "329.00", "qty": 200, "moq": 1, "lead": 26, "shipping": "40.00", "spec": {"tdp_w": 180}},
        ],
    },
    {
        "code": "kepler",
        "name": "Kepler CPUs",
        "email": "rfq@kepler.sandbox",
        "lines": [
            {"component": "cpu", "sku": "ACME-CPU-LGA", "price": "149.00", "qty": 200, "moq": 1, "lead": 8, "shipping": "25.00", "spec": {"socket": "LGA1700", "tdp_w": 125}},
        ],
    },
    {
        "code": "pinnacle",
        "name": "Pinnacle Boards",
        "email": "desk@pinnacle.sandbox",
        "lines": [
            {"component": "motherboard", "sku": "ACME-MB-B650", "price": "142.00", "qty": 160, "moq": 1, "lead": 9, "shipping": "45.00", "spec": {"socket": "AM5", "memory": "DDR5", "form_factor": "ATX"}},
            {"component": "motherboard", "sku": "ACME-MB-H610", "price": "96.00", "qty": 180, "moq": 1, "lead": 9, "shipping": "45.00", "spec": {"socket": "LGA1700", "memory": "DDR5", "form_factor": "mATX"}},
        ],
    },
    {
        "code": "harbor",
        "name": "Harbor Boards",
        "email": "sales@harbor.sandbox",
        "lines": [
            {"component": "motherboard", "sku": "ACME-MB-B650H", "price": "168.00", "qty": 140, "moq": 1, "lead": 12, "shipping": "50.00", "spec": {"socket": "AM5", "memory": "DDR5", "form_factor": "ATX"}},
        ],
    },
    {
        "code": "lattice",
        "name": "Lattice Memory",
        "email": "hello@lattice.sandbox",
        "lines": [
            {"component": "ram", "sku": "ACME-RAM-DDR5-16", "price": "36.00", "qty": 240, "moq": 1, "lead": 7, "shipping": "28.00", "spec": {"memory": "DDR5"}},
        ],
    },
    {
        "code": "summit",
        "name": "Summit Memory",
        "email": "orders@summit.sandbox",
        "lines": [
            {"component": "ram", "sku": "ACME-RAM-DDR5-16", "price": "44.00", "qty": 180, "moq": 1, "lead": 8, "shipping": "32.00", "spec": {"memory": "DDR5"}},
        ],
    },
    {
        "code": "redwood",
        "name": "Redwood Storage",
        "email": "rfq@redwood.sandbox",
        "lines": [
            {"component": "storage", "sku": "ACME-SSD-1TB", "price": "79.00", "qty": 200, "moq": 1, "lead": 6, "shipping": "22.00", "spec": {}},
        ],
    },
    {
        "code": "bytebarn",
        "name": "Bytebarn Storage",
        "email": "quotes@bytebarn.sandbox",
        "lines": [
            {"component": "storage", "sku": "ACME-SSD-1TB", "price": "92.00", "qty": 220, "moq": 1, "lead": 5, "shipping": "18.00", "spec": {}},
        ],
    },
    {
        "code": "voltworks",
        "name": "Voltworks Power",
        "email": "quotes@voltworks.sandbox",
        "lines": [
            {"component": "psu", "sku": "ACME-PSU-750", "price": "88.00", "qty": 150, "moq": 1, "lead": 11, "shipping": "30.00", "spec": {"wattage": 750}},
        ],
    },
    {
        "code": "ampere",
        "name": "Ampere Electric",
        "email": "rfq@ampere.sandbox",
        "lines": [
            {"component": "psu", "sku": "ACME-PSU-450", "price": "59.00", "qty": 200, "moq": 1, "lead": 7, "shipping": "24.00", "spec": {"wattage": 450}},
        ],
    },
    {
        "code": "casewright",
        "name": "Casewright",
        "email": "sales@casewright.sandbox",
        "lines": [
            {"component": "case", "sku": "ACME-CASE-ATX", "price": "64.00", "qty": 170, "moq": 1, "lead": 12, "shipping": "55.00", "spec": {"form_factors": ["ATX", "mATX"]}},
        ],
    },
    {
        "code": "microshell",
        "name": "MicroShell",
        "email": "orders@microshell.sandbox",
        "lines": [
            {"component": "case", "sku": "ACME-CASE-MATX", "price": "48.00", "qty": 190, "moq": 1, "lead": 9, "shipping": "40.00", "spec": {"form_factors": ["mATX"]}},
        ],
    },
    {
        "code": "aerocool",
        "name": "AeroCool Supply",
        "email": "rfq@aerocool.sandbox",
        "lines": [
            {"component": "cooling", "sku": "ACME-COOL-AM5", "price": "39.00", "qty": 160, "moq": 1, "lead": 9, "shipping": "18.00", "spec": {"sockets": ["AM5"]}},
        ],
    },
    {
        "code": "frostpeak",
        "name": "FrostPeak Cooling",
        "email": "sales@frostpeak.sandbox",
        "lines": [
            {"component": "cooling", "sku": "ACME-COOL-LGA", "price": "29.00", "qty": 180, "moq": 1, "lead": 8, "shipping": "16.00", "spec": {"sockets": ["LGA1700"]}},
        ],
    },
]

ACTORS = [
    {"key": "maya", "name": "Maya Chen", "role": "procurement_manager", "phone": "+15555550100"},
    {"key": "jordan", "name": "Jordan Hale", "role": "controller", "phone": "+15555550101"},
    {"key": "sam", "name": "Sam Ortiz", "role": "requester", "phone": "+15555550102"},
]

BETA_ACTOR = {"key": "riley", "name": "Riley Quinn", "role": "procurement_manager", "phone": "+15555550200"}
