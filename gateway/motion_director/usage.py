"""Allowlisted CLI observations. Unknown provider requests/retries stay unknown."""
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OpusUsage(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    invocation_id: str
    cli_invocations: int = 1
    application_retries: int = 0
    provider_requests: int | None = None
    observed_retry_events: int | None = None
    assistant_messages: int | None = None
    reported_turns: int | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cache_read_input_tokens: int | None = Field(default=None, ge=0)
    cache_creation_input_tokens: int | None = Field(default=None, ge=0)
    cli_seconds: float = Field(ge=0)
    reported_api_seconds: float | None = None
    status: str = Field(max_length=60)
    billing: Literal['subscription; API cost not applicable'] = 'subscription; API cost not applicable'


class PlanningUsage(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    source: Literal['opus', 'saved-plan', 'scene-reuse', 'local-camera', 'local']
    new_claude_invocations: int = Field(ge=0, le=1)
    elapsed_seconds: float = Field(ge=0)
    original: OpusUsage | None = None


def observe(stdout, invocation_id, seconds, status='completed'):
    events = []
    try:
        value = json.loads(stdout)
        events = value if isinstance(value, list) else [value]
    except ValueError:
        for line in stdout.splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
    events = [e for e in events if isinstance(e, dict)]
    result = next((e for e in reversed(events) if e.get('type')=='result'), None)
    if result is None and len(events)==1 and 'type' not in events[0]:
        result = events[0]  # auth status / older terminal JSON
    usage = (result or {}).get('usage', {})
    if not isinstance(usage, dict):
        usage = {}
    def count(key):
        value = usage.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) and value>=0 else None
    messages = {e.get('message', {}).get('id') for e in events if e.get('type')=='assistant' and e.get('message', {}).get('id')}
    retries = [e for e in events if e.get('type')=='system' and e.get('subtype')=='api_retry']
    api_ms = (result or {}).get('duration_api_ms')
    observed = OpusUsage(invocation_id=invocation_id, cli_seconds=round(seconds,3), status=status,
        assistant_messages=len(messages) if messages else None,
        observed_retry_events=len(retries) if retries else None,
        reported_turns=(result or {}).get('num_turns'),
        reported_api_seconds=api_ms/1000 if isinstance(api_ms,(int,float)) and api_ms>=0 else None,
        **{k:count(k) for k in ('input_tokens','output_tokens','cache_read_input_tokens','cache_creation_input_tokens')})
    return result, observed


def record(usage):
    path = Path(os.getenv('NEONFORGE_MOTION_USAGE_LOG',str(Path(__file__).resolve().parents[2]/'.motion-runtime/opus-usage.jsonl')))
    path.parent.mkdir(parents=True, exist_ok=True)
    # No prompts, images, credentials, CLI stderr, account/session IDs or raw events.
    with os.fdopen(os.open(path, os.O_WRONLY|os.O_CREAT|os.O_APPEND, 0o600),'a') as stream:
        stream.write(usage.model_dump_json()+'\n')
