CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS companies (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    currency CHAR(3) NOT NULL DEFAULT 'USD',
    timezone TEXT NOT NULL DEFAULT 'America/Chicago',
    ship_to TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS actors (
    id UUID PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('requester', 'procurement_manager', 'controller', 'system_agent', 'gateway')),
    phone_e164 TEXT NOT NULL UNIQUE,
    active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS procurement_tasks (
    id UUID PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    created_by UUID REFERENCES actors (id),
    public_code TEXT NOT NULL UNIQUE,
    item_spec_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    quantity INTEGER,
    budget_amount NUMERIC(14, 2),
    currency CHAR(3) NOT NULL DEFAULT 'USD',
    delivery_deadline DATE,
    destination TEXT,
    status TEXT NOT NULL CHECK (status IN (
        'CREATED', 'NEEDS_INFO', 'SOURCING', 'AWAITING_QUOTES', 'EVALUATING',
        'AWAITING_APPROVAL', 'ORDERING', 'AWAITING_CONFIRMATION', 'COMPLETED',
        'BLOCKED', 'CANCELLED'
    )),
    version INTEGER NOT NULL DEFAULT 1,
    preferences_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    assumptions_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    blocked_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bom_versions (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES procurement_tasks (id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    UNIQUE (task_id, version)
);

CREATE TABLE IF NOT EXISTS bom_lines (
    id UUID PRIMARY KEY,
    bom_version_id UUID NOT NULL REFERENCES bom_versions (id) ON DELETE CASCADE,
    component_type TEXT NOT NULL,
    required_sku TEXT NOT NULL,
    units_per_computer INTEGER NOT NULL CHECK (units_per_computer > 0),
    allowed_substitutes_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    spec_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    position INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS component_compatibility_rules (
    id UUID PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    rule_type TEXT NOT NULL,
    constraint_json JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS suppliers (
    id UUID PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    code TEXT NOT NULL,
    contact_email TEXT NOT NULL,
    approved BOOLEAN NOT NULL DEFAULT FALSE,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (company_id, code)
);

CREATE TABLE IF NOT EXISTS supplier_catalog (
    id UUID PRIMARY KEY,
    supplier_id UUID NOT NULL REFERENCES suppliers (id) ON DELETE CASCADE,
    sku TEXT NOT NULL,
    component_type TEXT NOT NULL,
    spec_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    indicative_price NUMERIC(14, 2) NOT NULL,
    available_qty INTEGER NOT NULL,
    min_order_qty INTEGER NOT NULL DEFAULT 1,
    indicative_lead_days INTEGER NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (supplier_id, sku)
);

CREATE TABLE IF NOT EXISTS rfqs (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES procurement_tasks (id) ON DELETE CASCADE,
    supplier_id UUID NOT NULL REFERENCES suppliers (id),
    correlation_token TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('draft', 'sent', 'received', 'needs_review', 'closed', 'expired')),
    sent_at TIMESTAMPTZ,
    response_due_at TIMESTAMPTZ,
    requested_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS rfqs_one_open_per_supplier
    ON rfqs (task_id, supplier_id)
    WHERE status IN ('draft', 'sent', 'needs_review');

CREATE TABLE IF NOT EXISTS messages (
    id UUID PRIMARY KEY,
    company_id UUID REFERENCES companies (id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    provider_message_id TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    actor_id UUID REFERENCES actors (id),
    rfq_id UUID REFERENCES rfqs (id),
    task_id UUID REFERENCES procurement_tasks (id) ON DELETE CASCADE,
    raw_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    normalized_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider, provider_message_id)
);

CREATE TABLE IF NOT EXISTS supplier_quotes (
    id UUID PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    task_id UUID REFERENCES procurement_tasks (id) ON DELETE CASCADE,
    rfq_id UUID REFERENCES rfqs (id) ON DELETE CASCADE,
    supplier_id UUID NOT NULL REFERENCES suppliers (id),
    bom_line_id UUID REFERENCES bom_lines (id),
    sku TEXT NOT NULL,
    component_type TEXT,
    unit_price NUMERIC(14, 2),
    currency CHAR(3) NOT NULL DEFAULT 'USD',
    available_qty INTEGER,
    min_order_qty INTEGER,
    shipping_amount NUMERIC(14, 2),
    taxes_amount NUMERIC(14, 2),
    estimated_arrival DATE,
    valid_until DATE,
    evidence_ref TEXT,
    status TEXT NOT NULL CHECK (status IN ('REQUESTED', 'RECEIVED', 'NEEDS_REVIEW', 'VALID', 'EXPIRED', 'REJECTED')),
    version INTEGER NOT NULL DEFAULT 1,
    spec_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    uncertainty_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS purchase_plans (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES procurement_tasks (id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    plan_hash TEXT NOT NULL,
    total_landed_cost NUMERIC(14, 2),
    currency CHAR(3) NOT NULL DEFAULT 'USD',
    assumptions_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    alternatives_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL CHECK (status IN ('proposed', 'invalidated', 'approved', 'rejected', 'executed')),
    readiness_computers INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (task_id, version)
);

CREATE TABLE IF NOT EXISTS purchase_plan_lines (
    id UUID PRIMARY KEY,
    plan_id UUID NOT NULL REFERENCES purchase_plans (id) ON DELETE CASCADE,
    bom_line_id UUID REFERENCES bom_lines (id),
    supplier_id UUID NOT NULL REFERENCES suppliers (id),
    quote_id UUID REFERENCES supplier_quotes (id),
    sku TEXT NOT NULL,
    component_type TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price NUMERIC(14, 2) NOT NULL,
    extended_cost NUMERIC(14, 2) NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES procurement_tasks (id) ON DELETE CASCADE,
    plan_id UUID NOT NULL REFERENCES purchase_plans (id),
    plan_hash TEXT NOT NULL,
    requested_from UUID NOT NULL REFERENCES actors (id),
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'expired', 'invalidated')),
    expires_at TIMESTAMPTZ NOT NULL,
    responded_at TIMESTAMPTZ,
    response_message_id UUID,
    token TEXT NOT NULL UNIQUE,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES procurement_tasks (id) ON DELETE CASCADE,
    plan_id UUID NOT NULL REFERENCES purchase_plans (id),
    supplier_id UUID NOT NULL REFERENCES suppliers (id),
    po_number TEXT NOT NULL UNIQUE,
    amount NUMERIC(14, 2) NOT NULL,
    currency CHAR(3) NOT NULL DEFAULT 'USD',
    status TEXT NOT NULL CHECK (status IN (
        'DRAFT', 'AUTHORIZED', 'SENDING', 'SENT', 'ACKNOWLEDGED', 'REJECTED', 'CANCELLED'
    )),
    idempotency_key TEXT NOT NULL UNIQUE,
    provider_ref TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (plan_id, supplier_id)
);

CREATE TABLE IF NOT EXISTS outbox (
    id UUID PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL CHECK (status IN ('pending', 'sending', 'sent', 'failed', 'ambiguous', 'dead')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_until TIMESTAMPTZ,
    provider_ref TEXT,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS work_events (
    id UUID PRIMARY KEY,
    company_id UUID REFERENCES companies (id) ON DELETE CASCADE,
    task_id UUID REFERENCES procurement_tasks (id) ON DELETE CASCADE,
    actor_type TEXT NOT NULL,
    actor_id UUID,
    action TEXT NOT NULL,
    previous_state TEXT,
    new_state TEXT,
    reason TEXT,
    diff_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_refs_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    allowed BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS worker_jobs (
    id UUID PRIMARY KEY,
    company_id UUID REFERENCES companies (id) ON DELETE CASCADE,
    task_id UUID REFERENCES procurement_tasks (id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'leased', 'done', 'dead')),
    lease_until TIMESTAMPTZ,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_run_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    idempotency_key TEXT,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS worker_jobs_active_idem
    ON worker_jobs (idempotency_key)
    WHERE status IN ('pending', 'leased') AND idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS sandbox_deliveries (
    id UUID PRIMARY KEY,
    company_id UUID REFERENCES companies (id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    provider_ref TEXT NOT NULL,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tasks_company ON procurement_tasks (company_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_task ON work_events (task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_quotes_task ON supplier_quotes (task_id, sku);
CREATE INDEX IF NOT EXISTS idx_outbox_due ON outbox (status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_jobs_due ON worker_jobs (status, next_run_at);

CREATE OR REPLACE FUNCTION forbid_work_event_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'work_events is append-only';
END;
$$;

DROP TRIGGER IF EXISTS work_events_no_update ON work_events;
CREATE TRIGGER work_events_no_update
    BEFORE UPDATE OR DELETE ON work_events
    FOR EACH ROW EXECUTE FUNCTION forbid_work_event_mutation();
