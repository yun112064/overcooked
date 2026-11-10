"""Scoped dashboard summaries from the application database and local configuration."""
import os
from time import perf_counter

import sqlalchemy as sa

from app.db.models import AdoptedMealPlan, MealConfirmationEvent
from app.jobs.scheduler import (
    LOG_CLEANUP_HOUR, LOG_CLEANUP_MINUTE, MAINTENANCE_INTERVAL_SECONDS, SCHEDULER_TIMEZONE,
)
from app.management.contracts import POLICY
from app.management.dashboard_read import dated_page, scoped_tasks, visible_domains
from app.management.models import tables as T
from app.management.service import now, stamp
from app.pricing_scheduler import SchedulerSettings


def meal_confirmation_flows(s, limit):
    s.require('operations.read')
    event = MealConfirmationEvent
    recent = (sa.select(event.plan_id, event.occurred_at.label('confirmed_at'))
              .where(event.stage == 'confirmed')
              .order_by(event.occurred_at.desc(), event.plan_id.desc())
              .limit(limit).subquery())
    statement = (sa.select(event, AdoptedMealPlan.mode)
                 .join(recent, recent.c.plan_id == event.plan_id)
                 .join(AdoptedMealPlan, AdoptedMealPlan.id == event.plan_id)
                 .order_by(recent.c.confirmed_at.desc(), recent.c.plan_id.desc(), event.sequence))
    traces = {}
    for item, mode in s.db.execute(statement):
        trace_id = str(item.plan_id)
        trace = traces.setdefault(trace_id, {'trace_id': trace_id, 'mode': mode, 'events': []})
        trace['events'].append({
            'sequence': item.sequence, 'stage': item.stage, 'occurred_at': stamp(item.occurred_at),
        })
    return {'observed_at': stamp(now()), 'items': list(traces.values())}


def overview(s):
    s.require('operations.read')
    domains = visible_domains(s)
    proposals = T['proposals']
    counts = {domain: {'submitted': 0, 'changes_requested': 0} for domain in domains}
    for domain, state, total in s.db.execute(
        sa.select(proposals.c.domain, proposals.c.state, sa.func.count())
        .where(proposals.c.domain.in_(domains), proposals.c.state.in_(('submitted', 'changes_requested')))
        .group_by(proposals.c.domain, proposals.c.state)
    ):
        counts[domain][state] = total
    failed = None
    if domains:
        failed = s.db.scalar(sa.select(sa.func.count()).select_from(
            scoped_tasks(domains).where(T['tasks'].c.state == 'failed').subquery()))
    return {'observed_at': stamp(now()), 'proposals': counts, 'failed_validation_tasks': failed}


def database_status(s):
    s.require('operations.read')
    if s.db.get_bind().dialect.name != 'postgresql':
        return dict(status='not_configured', checked_at=None, query_latency_ms=None,
                    database_bytes=None, client_connections=None)
    start = perf_counter()
    row = s.db.execute(sa.text('''
        SELECT pg_database_size(current_database()), numbackends
        FROM pg_stat_database WHERE datname = current_database()
    ''')).one()
    return dict(status='healthy', checked_at=stamp(now()),
                query_latency_ms=round((perf_counter() - start) * 1000, 2),
                database_bytes=row[0], client_connections=row[1])


def schedule_configuration(s):
    s.require('operations.read')
    items = [
        dict(key=key, interval_seconds=MAINTENANCE_INTERVAL_SECONDS)
        for key in ('recover_expired_jobs', 'expire_queued_jobs')
    ]
    items.append(dict(key='log_cleanup', hour=LOG_CLEANUP_HOUR, minute=LOG_CLEANUP_MINUTE,
                      timezone=SCHEDULER_TIMEZONE,
                      configured_mode='apply' if os.getenv('LOG_CLEANUP_APPLY', 'false').lower() == 'true' else 'dry_run'))
    if 'price' in visible_domains(s):
        try:
            price = SchedulerSettings.from_env()
        except (ValueError, OverflowError):
            items.append(dict(key='price_collection', configured_mode='invalid'))
        else:
            items.append(dict(key='price_collection', hour=price.hour, minute=price.minute,
                              timezone=price.timezone_name, configured_mode='enabled' if price.enabled else 'disabled'))
    return dict(observed_at=stamp(now()), items=items)


def scheduled_price_runs(s, limit):
    s.require('price.read', 'price')
    s.require('operations.read')
    if s.db.get_bind().dialect.name != 'postgresql':
        return dict(observed_at=stamp(now()), status='not_configured', items=[])
    rows = s.db.execute(sa.text('''
        SELECT run_key, scheduled_for, started_at, finished_at, status, duration_ms,
               ingredient_total, success_count, failure_count, fatal_error IS NOT NULL AS has_error
        FROM price_collection_runs WHERE trigger_type = 'scheduled'
        ORDER BY started_at DESC, id DESC LIMIT :limit
    '''), {'limit': limit}).mappings()
    return dict(observed_at=stamp(now()), status='available', items=[
        dict(row, scheduled_for=stamp(row['scheduled_for']), started_at=stamp(row['started_at']),
             finished_at=stamp(row['finished_at']) if row['finished_at'] else None)
        for row in rows
    ])


def audit_events(s, domain, limit, cursor):
    s.require('audit.read', domain)
    domains = sorted({name for grant in s.grants() if 'audit.read' in POLICY['role_presets'][grant['role']]
                      for name in grant['domain_scope']})
    events = T['audit_events']
    query = sa.select(events).where(events.c.domain.in_([domain] if domain else domains))
    result = dated_page(s, query, events, limit, cursor, ['audit-events', domains, domain], lambda row: {
        'event_id': row['id'], 'actor_id': row['actor_id'], 'domain': row['domain'],
        'resource_id': row['resource_id'], 'action': row['action'], 'reason': row['reason'],
        'created_at': stamp(row['created_at']),
    })
    return dict(result, domains=domains)


def review_history(s, proposal, limit, cursor):
    reviews = T['review_decisions']
    query = sa.select(reviews).where(reviews.c.proposal_id == proposal['id'])
    return dated_page(s, query, reviews, limit, cursor, ['review-history', proposal['id']], lambda row: {
        'decision_id': row['id'], 'revision': row['revision'], 'scope': row['scope'],
        'verdict': row['verdict'], 'reason': row['reason'], 'created_at': stamp(row['created_at']),
    })
