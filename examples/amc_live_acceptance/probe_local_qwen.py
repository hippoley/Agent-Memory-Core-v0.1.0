#!/usr/bin/env python3
"""Non-actuating acceptance gate for a real local Qwen tool call.

Uses the same production `choose_device_action` code as Agent B. Never opens
an AMC session, calls a device gateway, or fabricates a model response.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import secrets
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

import httpx


def verify_local_url(endpoint: str):
    parsed = urlsplit(endpoint)
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('Qwen endpoint must use loopback HTTP')
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Qwen endpoint must not embed credentials or query strings')
    if parsed.path != '/v1/chat/completions':
        raise ValueError('Qwen endpoint must be /v1/chat/completions')


def verify_local_model(client, endpoint: str, pinned: str) -> dict:
    verify_local_url(endpoint)
    base = endpoint.removesuffix('/v1/chat/completions')
    response = client.get(base + '/v1/models')
    response.raise_for_status()
    data = response.json()
    available = data.get('data') if isinstance(data, dict) else None
    if (not isinstance(available, list) or
        not any(isinstance(m, dict) and m.get('id') == pinned for m in available)):
        raise ValueError('Pinned model ID absent from live /v1/models')
    return {'model_id': pinned, 'models_url': base + '/v1/models'}


class EvidenceClient:
    """Capture content hashes, not raw prompts or credentials, for one inference."""

    def __init__(self, underlying):
        self.underlying = underlying
        self.events = []

    def get(self, *args, **kwargs):
        return self.underlying.get(*args, **kwargs)

    def post(self, url, *, json, **kwargs):
        request_bytes = canonical(json).encode('utf-8')
        start = time.monotonic()
        response = self.underlying.post(url, json=json, **kwargs)
        response_bytes = response.content
        self.events.append({
            'request_sha256': hashlib.sha256(request_bytes).hexdigest(),
            'response_sha256': hashlib.sha256(response_bytes).hexdigest(),
            'http_status': response.status_code,
            'elapsed_ms': round((time.monotonic() - start) * 1000, 2),
        })
        return response


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False)


def challenge_targets(primary: int | float, *, count: int = 3, randbelow=None):
    """Vary requested setpoints; never permit a fixed 26-degree stub to pass."""
    if type(primary) not in (int, float) or not -1000 <= primary <= 1000:
        raise ValueError('Finite numeric target required')
    if type(count) is not int or count < 2 or count > 8:
        raise ValueError('Challenge count must be between two and eight')
    choose = randbelow or secrets.randbelow
    # 0.1-unit increments to preserve numeric semantics and avoid narrow choices.
    candidates = [round(primary + offset / 10, 3) for offset in (-3, -2, -1, 1, 2, 3)
                  if -1000 <= primary + offset/10 <= 1000]
    candidates = [v for v in candidates if v != primary]
    if len(candidates) < count - 1:
        raise ValueError('Not enough challenge values')
    selected = [primary]
    while len(selected) < count:
        idx = choose(len(candidates))
        selected.append(candidates.pop(idx))
    return selected


def probe(*, client, endpoint, model, device_id, property_name, expected_value):
    """Output is a restricted plan, NOT an effect receipt."""
    from agent_memory_core.handoff_model_agent import choose_device_action
    verify_local_model(client, endpoint, model)
    claim = {'id': 'preflight-intent-not-an-amc-claim', 'subject_id': device_id,
             'predicate': 'desired_' + property_name,
             'value': {property_name: expected_value}}
    plan = choose_device_action(
        accepted_payload={'claims': [claim]}, device_id=device_id,
        property_name=property_name, model=model, endpoint=endpoint,
        http_client=client)
    if plan['value'] != expected_value or plan['observed_model'] != model:
        raise ValueError('Live model response failed preflight contract')
    return {'model_id': model, 'decision': {'tool': 'set_device_property',
            'device_id': device_id, 'property': property_name,
            'value': expected_value}, 'effect_performed': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Live local Qwen preflight, no AMC/device writes')
    parser.add_argument('--device', default='preflight-bedroom')
    parser.add_argument('--property', dest='property_name', default='temperature')
    parser.add_argument('--target', type=json.loads, default=26)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--challenges', type=int, default=3)
    args = parser.parse_args(argv)
    if not isinstance(args.device, str) or not args.device or not isinstance(args.property_name, str) or not args.property_name:
        parser.error('Device and property must be non-empty')
    if type(args.target) not in (int, float) or not -1000 <= args.target <= 1000:
        parser.error('Target must be a finite JSON number')
    endpoint = os.environ.get('QWEN_CHAT_COMPLETIONS_URL', '').strip()
    model = os.environ.get('QWEN_MODEL_ID', '').strip()
    if not endpoint or not model:
        parser.error('QWEN_CHAT_COMPLETIONS_URL and QWEN_MODEL_ID required')
    report = {'schema': 'amc-qwen-preflight-v1',
              'time_utc': datetime.now(timezone.utc).isoformat(),
              'model_requested': model, 'status': 'failed_or_unverified',
              'live_endpoint_response_observed': False,
              'model_weights_independently_verified': False, 'effect_performed': False}
    try:
        targets = challenge_targets(args.target, count=args.challenges)
        with httpx.Client(timeout=120, trust_env=False) as client:
            tracked = EvidenceClient(client)
            results = [probe(client=tracked, endpoint=endpoint, model=model,
                             device_id=args.device, property_name=args.property_name,
                             expected_value=target) for target in targets]
        if len(tracked.events) != len(targets):
            raise ValueError('Every challenge must result in a distinct model request')
        report.update(status='model_tool_calls_observed', live_endpoint_response_observed=True,
                      observed={'results': results, 'challenge_targets': targets,
                                'inference_events': tracked.events,
                                'independent_model_weights_verified': False})
        report['canonical_result_sha256'] = hashlib.sha256(
            canonical(report['observed']).encode('utf-8')).hexdigest()
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        report['error'] = 'Local model preflight failed; see private runtime logs.'
        print('Preflight failed: ' + type(exc).__name__, file=sys.stderr)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'output': str(args.output),
                      'effect_performed': False}, ensure_ascii=False))
    return 0 if report['status'] == 'model_tool_calls_observed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
