-- Synthetic legacy schema fixture. Not a recovered historical migration.

CREATE TABLE public.admin_recipe_proposals (
    id character varying(32) NOT NULL,
    revision integer NOT NULL,
    status character varying(24) NOT NULL,
    author character varying(64) NOT NULL,
    reviewer character varying(64),
    base_release character varying(64) NOT NULL,
    content json NOT NULL,
    content_hash character varying(64) NOT NULL,
    approved_hash character varying(64),
    approved_dependencies character varying(64),
    recipe_id character varying(32),
    created_at timestamp with time zone NOT NULL
);

ALTER TABLE ONLY public.admin_recipe_proposals
    ADD CONSTRAINT admin_recipe_proposals_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.admin_recipe_proposals
    ADD CONSTRAINT admin_recipe_proposals_recipe_id_key UNIQUE (recipe_id);

CREATE TABLE public.admin_review_events (
    id character varying(32) NOT NULL,
    proposal_id character varying(32) NOT NULL,
    actor character varying(64) NOT NULL,
    action character varying(24) NOT NULL,
    details json NOT NULL,
    created_at timestamp with time zone NOT NULL
);

ALTER TABLE ONLY public.admin_review_events
    ADD CONSTRAINT admin_review_events_pkey PRIMARY KEY (id);

CREATE TABLE public.admin_schedule_runs (
    id character varying(32) NOT NULL,
    job_id character varying(100) NOT NULL,
    status character varying(24) NOT NULL,
    started_at timestamp with time zone NOT NULL,
    finished_at timestamp with time zone,
    message text
);

ALTER TABLE ONLY public.admin_schedule_runs
    ADD CONSTRAINT admin_schedule_runs_pkey PRIMARY KEY (id);

CREATE TABLE public.admin_scheduler_heartbeat (
    id character varying(32) NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    schedules json NOT NULL
);

ALTER TABLE ONLY public.admin_scheduler_heartbeat
    ADD CONSTRAINT admin_scheduler_heartbeat_pkey PRIMARY KEY (id);

CREATE INDEX ix_admin_review_events_proposal_id ON public.admin_review_events USING btree (proposal_id);

CREATE INDEX ix_admin_schedule_runs_job_id ON public.admin_schedule_runs USING btree (job_id);

CREATE INDEX ix_admin_schedule_runs_started_at ON public.admin_schedule_runs USING btree (started_at);
