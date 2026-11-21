"""Passive timestamped context observations, including cycles with no signal.

Observing a payload now does not prove it was available earlier. Querying a
gap or an observation after the requested decision always returns unknown.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from megalord.research.trade_contract import utc_time


class ContextArchive:
    def __init__(self, path, *, interval_seconds=60):
        self.path = Path(path)
        self.interval_seconds = interval_seconds
        self.last_time = None
        self.last_hash = None

    def observe(self, *, observed_at, macro_gates, news_config, news_refreshed_at):
        at=utc_time(observed_at)
        if self.last_time is not None and at < self.last_time:
            raise ValueError('Context observation clock went backwards')
        payload=dict(macro_gates=deepcopy(macro_gates), news_config=deepcopy(news_config),
                     news_refreshed_at=utc_time(news_refreshed_at).isoformat() if news_refreshed_at is not None else None)
        raw=json.dumps(payload,sort_keys=True,allow_nan=False,separators=(',',':'))
        digest=sha256(raw.encode()).hexdigest()
        if self.last_time is not None and digest==self.last_hash and (at-self.last_time).total_seconds()<self.interval_seconds:
            return False
        row=dict(schema_version=1,source_kind='CONTEXT_OBSERVATION',available_at=at.isoformat(),
                 payload_sha256=digest,payload=payload,upstream_causality_verified=False,
                 calendar_completeness_verified=False)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.path.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(row,sort_keys=True,allow_nan=False)+'\n')
        self.last_time,self.last_hash=at,digest
        return True


def context_asof(records, decision_at, *, max_age_seconds=120):
    at=utc_time(decision_at)
    eligible=[r for r in records if r.get('source_kind')=='CONTEXT_OBSERVATION' and utc_time(r['available_at'])<=at]
    if not eligible:return None
    row=max(eligible,key=lambda r:utc_time(r['available_at']))
    if (at-utc_time(row['available_at'])).total_seconds()>max_age_seconds:return None
    encoded=json.dumps(row['payload'],sort_keys=True,allow_nan=False,separators=(',',':'))
    if sha256(encoded.encode()).hexdigest()!=row['payload_sha256']:
        raise ValueError('Context content fingerprint mismatch')
    return deepcopy(row)
