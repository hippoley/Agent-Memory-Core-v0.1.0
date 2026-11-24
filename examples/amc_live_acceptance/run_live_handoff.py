#!/usr/bin/env python3
"""Real-endpoint smoke runner for AMC PR #65.

Needs: pip install -e '.[api,postgres]' and httpx.
Run on the machine where the pinned local Qwen model is hosted.
Does not fabricate model outputs or device observations.  No real hardware
success may be claimed without independently audited gateway deployments.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import secrets
from urllib.parse import urlparse
import httpx


def env(name: str) -> str:
    value = os.environ.get(name, '').strip()
    if not value:
        raise ValueError(f'Missing environment variable: {name}')
    return value


def validate_endpoints(model_endpoint, actuator_endpoint, observer_endpoint):
    m, a, o = map(urlparse, (model_endpoint, actuator_endpoint, observer_endpoint))
    if m.scheme != 'http' or m.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('Model must be accessed through localhost HTTP on the model machine')
    for label, parsed in [('actuator', a), ('observer', o)]:
        if parsed.scheme != 'https' or not parsed.hostname:
            raise ValueError(f'{label} endpoint must be an HTTPS URL')
        if parsed.username or parsed.password or parsed.fragment or parsed.query:
            raise ValueError(f'{label} URL must not embed credentials or query parameters')
    if (a.scheme, a.netloc) == (o.scheme, o.netloc):
        raise ValueError('Command and observer must use distinct HTTPS origins')
    # Distinct HTTPS origins are a minimum check, not proof of independent data paths.


def response_property(body: object, *, device_id: str, property_name: str):
    if not isinstance(body, dict) or body.get('device_id') != device_id:
        raise ValueError('Readback has absent/mismatched device_id')
    if property_name not in body or body.get('source') not in ('hardware', 'independent_gateway'):
        raise ValueError('Readback missing property or independently documented source')
    return body[property_name]


def flush_report(outcome: dict, output: Path):
    # Atomic replace of this run's reserved slot. Not tamper-resistant; retain
    # operator logs separately and do not use this file as signed attestation.
    tmp = output.with_name(output.name + '.tmp')
    tmp.write_text(json.dumps(outcome, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
    tmp.replace(output)


def main() -> int:
    p = argparse.ArgumentParser(description='AMC real-model + independent-observer acceptance')
    p.add_argument('--device', required=True)
    p.add_argument('--property', dest='property_name', required=True)
    p.add_argument('--target', required=True, type=json.loads, help='JSON number, e.g. 26 or 26.5')
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--confirm-live-device-write', action='store_true')
    p.add_argument('--safe-min', required=True, type=float, help='Authorized lower device setpoint bound')
    p.add_argument('--safe-max', required=True, type=float, help='Authorized upper device setpoint bound')
    args = p.parse_args()
    if type(args.target) not in (int, float) or not -1000 <= args.target <= 1000:
        p.error('Target must be a finite JSON number in the supported guard range')
    if not (-1000 <= args.safe_min <= args.safe_max <= 1000 and args.safe_min <= args.target <= args.safe_max):
        p.error('Target outside explicitly authorized safe-min/safe-max range')
    if not args.confirm_live_device_write:
        p.error('Live actuation requires --confirm-live-device-write')

    amc_url, amc_key, tenant = env('MEMORY_API_URL'), env('MEMORY_API_KEY'), env('MEMORY_TENANT_ID')
    model_endpoint, model_id = env('QWEN_CHAT_COMPLETIONS_URL'), env('QWEN_MODEL_ID')
    command_url, observation_url = env('DEVICE_COMMAND_URL'), env('DEVICE_OBSERVER_URL')
    command_token, observer_token = env('DEVICE_COMMAND_TOKEN'), env('DEVICE_OBSERVER_TOKEN')
    validate_endpoints(model_endpoint, command_url, observation_url)
    # Independent credentials are useful but cannot establish true hardware independence.
    if command_token == observer_token:
        raise ValueError('Command and observer must use distinct credentials')

    # Reserve output BEFORE creating a task or touching hardware. An existing
    # result is never overwritten, including a failed/UNKNOWN run.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with args.output.open('x', encoding='utf-8') as f:
            f.write(json.dumps({'schema':'amc-live-v4', 'status':'reserved',
                                'created_at':datetime.now(timezone.utc).isoformat()})+'\n')
    except FileExistsError as exc:
        raise ValueError('Existing report; refusing replay or overwrite') from exc

    from agent_memory_core.sdk import MemoryClient
    from agent_memory_core.handoff_model_agent import execute_model_guided_handoff
    started = datetime.now(timezone.utc).isoformat()
    outcome = {'schema':'amc-live-v4', 'run_id': secrets.token_hex(16),
               'started_at': started, 'run_type': 'live_endpoint_smoke',
               'safe_bounds': [args.safe_min, args.safe_max],
               'phase':'preflight', 'actuation_attempted':False,
               'actuation_acknowledged':False, 'readback_after_attempt_observed':False,
               'device_id': args.device, 'property': args.property_name,
               'target': args.target, 'model_requested': model_id,
               'independence_attestation': 'two_origins_and_credentials_only_not_physically_proven',
               'model_weights_independently_verified': False,
               'observer_physical_independence_verified': False}

    def write_device(device_id, property_name, target):
        outcome['phase'] = 'actuation_attempted'
        outcome['actuation_attempted'] = True  # before network I/O; outcome could be UNKNOWN
        flush_report(outcome, args.output)
        with httpx.Client(timeout=15) as client:
            r = client.post(command_url,
                            headers={'Authorization': 'Bearer ' + command_token},
                            json={'device_id': device_id, 'property': property_name, 'value': target})
            r.raise_for_status()
            outcome['actuation_acknowledged'] = True
            outcome['phase'] = 'awaiting_independent_readback'
            flush_report(outcome, args.output)
            # We deliberately do not use ACK as proof.
            return None

    def read_device(device_id, property_name):
        with httpx.Client(timeout=15) as client:
            r = client.get(observation_url,
                           headers={'Authorization': 'Bearer ' + observer_token},
                           params={'device_id': device_id, 'property': property_name})
            r.raise_for_status()
            observed = response_property(r.json(), device_id=device_id, property_name=property_name)
            if outcome['actuation_attempted']:
                outcome['readback_after_attempt_observed'] = True
                outcome['phase'] = 'post_command_readback'
                flush_report(outcome, args.output)
            return observed

    try:
        from probe_local_qwen import (probe as verify_model_tool_path,
                                      challenge_targets, EvidenceClient)
        challenges = challenge_targets(args.target, count=3)
        with httpx.Client(timeout=120, trust_env=False) as preflight_http:
            evidence_http = EvidenceClient(preflight_http)
            preflight = [verify_model_tool_path(
                client=evidence_http, endpoint=model_endpoint, model=model_id,
                device_id=args.device, property_name=args.property_name,
                expected_value=target) for target in challenges]
        if len(evidence_http.events) != len(challenges):
            raise ValueError('Not all independent preflight requests were observed')
        outcome['phase'] = 'model_preflight_passed'
        outcome["model_preflight"] = {
            'challenge_targets': challenges, 'results': preflight,
            'inference_events': evidence_http.events,
            'model_weights_independently_verified': False}
        flush_report(outcome, args.output)
        with MemoryClient(amc_url, amc_key, tenant_id=tenant) as a, \
             MemoryClient(amc_url, amc_key, tenant_id=tenant) as b, \
             httpx.Client(timeout=120) as llm_http:
            receipt = execute_model_guided_handoff(
                agent_a=a, agent_b=b, device_id=args.device,
                property_name=args.property_name, expected_value=args.target,
                actuator=write_device, observer=read_device,
                model=model_id, endpoint=model_endpoint, http_client=llm_http)
            outcome.update(phase='verified_endpoint_readback', status='verified_by_endpoint_readback',
                           receipt={k: getattr(receipt, k) for k in receipt.__dataclass_fields__})
    except Exception as exc:
        outcome.update(status=('unknown_effect' if outcome['actuation_attempted'] else 'failed_before_actuation'),
                       phase=('requires_manual_reconciliation' if outcome['actuation_attempted'] else 'stopped_without_actuation'),
                       error_type=type(exc).__name__,
                       error='Runtime failed; inspect private logs. Device state may be UNKNOWN.')
        print(f'Run failed: {type(exc).__name__}', file=sys.stderr)
    outcome['finished_at'] = datetime.now(timezone.utc).isoformat()
    flush_report(outcome, args.output)
    print(json.dumps({'status':outcome['status'], 'report':str(args.output)},ensure_ascii=False))
    return 0 if outcome['status'] == 'verified_by_endpoint_readback' else 1


if __name__ == '__main__':
    raise SystemExit(main())
