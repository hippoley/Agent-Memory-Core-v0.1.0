#!/usr/bin/env python3
"""Read-only follow-up for an AMC live operation whose effect is UNKNOWN.

This does NOT retry commands or assert causation. A matching value means only
that the observer reported the desired state at reconciliation time.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import httpx
from run_live_handoff import response_property, validate_endpoints


def _utc():
    return datetime.now(timezone.utc).isoformat()


def reconcile(*, prior_report: Path, output: Path, observer_url: str,
              observer_token: str, command_url: str, model_url: str,
              http_client=None):
    source = prior_report.read_bytes()
    prior = json.loads(source)
    if not isinstance(prior, dict) or prior.get('schema') != 'amc-live-v4':
        raise ValueError('Not an AMC v4 live report')
    attempted = prior.get('actuation_attempted') is True
    unknown = prior.get('status') == 'unknown_effect' or (
        prior.get('status') is None and prior.get('phase') in (
            'actuation_attempted', 'awaiting_independent_readback',
            'post_command_readback'))
    if not attempted or not unknown:
        raise ValueError('Reconcile is only for interrupted or UNKNOWN actuation')
    if not isinstance(prior.get('run_id'), str) or len(prior['run_id']) < 16:
        raise ValueError('Missing auditable originating run ID')
    device_id, prop = prior.get('device_id'), prior.get('property')
    target = prior.get('target')
    if (not isinstance(device_id, str) or not device_id
            or not isinstance(prop, str) or not prop
            or type(target) not in (int, float)):
        raise ValueError('Invalid source operation')
    validate_endpoints(model_url, command_url, observer_url)
    if not observer_token:
        raise ValueError('Observer authentication required')
    if output.resolve() == prior_report.resolve():
        raise ValueError('Reconciliation may not overwrite the source receipt')
    output.parent.mkdir(parents=True, exist_ok=True)
    # Reserve output before observer I/O. The only network operation below is GET.
    with output.open('x', encoding='utf-8') as f:
        f.write(json.dumps({'schema':'amc-reconciliation-v1',
                            'status':'reserved', 'created_at':_utc()})+'\n')
    result = {
        'schema':'amc-reconciliation-v1', 'original_run_id':prior['run_id'],
        'original_report_sha256':hashlib.sha256(source).hexdigest(),
        'device_id':device_id,'property':prop,'target':target,
        'observed_at':None,'status':'readback_unavailable',
        'device_command_retried':False,
        'causal_attribution_proven':False,
        'physical_observer_independence_proven':False,
    }
    try:
        if http_client is None:
            with httpx.Client(timeout=15, trust_env=False) as client:
                response = client.get(observer_url,
                    headers={'Authorization':'Bearer '+observer_token},
                    params={'device_id':device_id, 'property':prop})
        else:
            response = http_client.get(observer_url,
                headers={'Authorization':'Bearer '+observer_token},
                params={'device_id':device_id, 'property':prop})
        response.raise_for_status()
        body = response.json()
        observed = response_property(body,device_id=device_id,property_name=prop)
        if type(observed) is not type(target):
            raise ValueError('Observed value type differs from intended target')
        result.update(observed=observed,observed_at=_utc(),
                      response_sha256=hashlib.sha256(response.content).hexdigest(),
                      status=('state_matches_target' if observed == target
                              else 'state_differs_from_target'))
    except (httpx.HTTPError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        result['status']='readback_unavailable'
        result['error']='Observer readback unavailable or invalid; no retry attempted'
    # Replace only this run's exclusive output. Never mutate original evidence.
    tmp=output.with_name(output.name+'.tmp')
    tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    tmp.replace(output)
    return result


def main():
    parser=argparse.ArgumentParser(description='Read-only reconciliation of AMC UNKNOWN device effect')
    parser.add_argument('--report',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    result=reconcile(prior_report=args.report,output=args.output,
        model_url=os.environ['QWEN_CHAT_COMPLETIONS_URL'],
        command_url=os.environ['DEVICE_COMMAND_URL'],
        observer_url=os.environ['DEVICE_OBSERVER_URL'],
        observer_token=os.environ['DEVICE_OBSERVER_TOKEN'])
    print(json.dumps({'status':result['status'],'output':str(args.output)},ensure_ascii=False))
    return 0 if result['status'] in ('state_matches_target','state_differs_from_target') else 2

if __name__ == '__main__':
    sys.exit(main())
