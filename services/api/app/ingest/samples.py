SAMPLE_DOCUMENTS = [
    (
        "po-101.txt",
        """Purchase order PO-101
Vendor: Cloud Services Inc.
Date: 2025-09-12
Amount: $145.00
Cloud hosting for September.
""",
    ),
    (
        "em-207.txt",
        """From: Cloud Services Inc.
Date: 2025-09-25
Subject: Estimate EM-207
This email estimates the September charge for purchase order PO-101.
Estimate $160.00.
""",
    ),
    (
        "je-900.txt",
        """Journal entry JE-900
Vendor: Cloud Services Inc.
Date: 2025-09-30
Accrual amount $150.00
Reference purchase order PO-101.
No formula is preserved for this accrual.
""",
    ),
    (
        "inv-381.txt",
        """Invoice INV-381
Vendor: Cloud Services Inc.
Document date: 2025-09-28
Received: 2025-12-10
Total $158.00
Purchase order PO-101.
""",
    ),
]
