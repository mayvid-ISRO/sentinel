# Anomaly & Intrusion Detection (Phase 4)

**Feature**: Statistical anomaly detection over infrastructure telemetry — baseline tracking, z-score deviation alerts, and cooldown-based deduplication.

**Status**: Phase 4, shipped. 22 new tests (118 total).

**Files**:
- `anomalies/__init__.py` — Package exports
- `anomalies/baseline.py` — `BaselineTracker`: per-key rolling-window statistics (mean, std, z-score) with JSON persistence
- `anomalies/config.py` — `[events.anomaly]` config + env-var overrides (`IRIS_ANOMALY_*`)
- `anomalies/adapter.py` — `AnomalyAdapter`: subscribes to EventBus, runs `check()` on every numeric event, publishes `domain="security"` anomaly events
- Backend: `backend/main.py` (`/api/anomaly/*` endpoints)
- Frontend: Anomalies panel (below Event Radar), risk scoring in event feed

## Architecture

```
EventBus ──► AnomalyAdapter._on_event(event)
                 │
                 ├─ event.value is None? → skip
                 ├─ math.isfinite(value)? → skip if not
                 │
                 ▼
           BaselineTracker.check(source, entity, metric, value)
                 │
                 ├─ n < min_samples  → collect sample, return note="insufficient"
                 ├─ |z| < threshold  → normal, return is_anomaly=False
                 ├─ in cooldown      → suppress, return is_anomaly=False
                 └─ |z| >= threshold → ANOMALY! publish security event
                                          (back to bus → rules engine → tasks)
```

### Key = (source_type, entity, metric)

The tracker groups samples by a three-part key so different hosts/sensors don't mix:

| Source | Example key | Metric name |
|--------|-------------|-------------|
| metrics adapter | `"metrics"|"host-01"|"cpu"` | source_type used as metric |
| syslog adapter | `"syslog"|"fw-01"|"auth_failures"` | synthetic from log count |
| winlog adapter | `"winlog"|"dc01"|"event_rate"` | events/sec window |

Any event with a numeric `value` field gets checked against its key's baseline.

## BaselineTracker Statistics

Pure-Python rolling window — no numpy, no pandas, no sklearn.

- **Mean**: `sum(values) / n`
- **Std dev**: population std over the window (Bessel's correction: `n-1`)
- **Z-score**: `(value - mean) / std` — how many standard deviations away
- **Window**: Capped deque; oldest values evicted as new ones arrive
- **Persistence**: All window data saved to JSON after each observation; restored on restart
- **Cooldown**: After an anomaly fires, same key is suppressed for `cooldown_seconds` to prevent alert storms

## Configuration

Add to `config.json`:

```json
"events": {
  "anomaly": {
    "enabled": true,
    "window_size": 100,
    "z_threshold": 2.5,
    "min_samples": 10,
    "cooldown_seconds": 300,
    "store_path": ".iris/baselines.json"
  }
}
```

Environment variable overrides (highest precedence):

| Env var | Maps to | Example |
|---------|---------|---------|
| `IRIS_ANOMALY_ENABLED` | enabled | `true` |
| `IRIS_ANOMALY_Z_THRESH` | z_threshold | `3.0` |
| `IRIS_ANOMALY_WINDOW` | window_size | `200` |
| `IRIS_ANOMALY_MIN_SAMPLES` | min_samples | `5` |
| `IRIS_ANOMALY_COOLDOWN` | cooldown_seconds | `600` |
| `IRIS_ANOMALY_STORE` | store_path | `/mnt/data/baselines.json` |

When `enabled: false` (default), the adapter does not subscribe to the bus — zero overhead.

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/anomaly/config` | none | Current config (enabled, thresholds, store path) |
| GET | `/api/anomaly/baselines` | none | List all baselines with ≥min_samples data |
| DELETE | `/api/anomaly/baselines/{src}/{ent}/{met}` | none | Remove one baseline key |
| DELETE | `/api/anomaly/baselines` | none | Clear all baselines (reset detector) |
| GET | `/api/anomaly/recent` | none | Recent anomaly events from events table |

Anomaly events are stored in the existing `events` SQLite table with `source='anomaly'` and `domain='security'`, severity=`critical`. They flow through the normal event pipeline (rules engine → tasks).

## Frontend Integration

A new **Anomalies** panel sits below the Event Radar. It shows:
- Active anomaly count (red badge)
- List of recent anomalies with z-score, direction (↑/↓), baseline stats
- Click-to-clear individual baselines
- "Reset detector" button (clears all baselines, re-baselines from scratch)

Anomaly events in the Event Radar feed are color-coded red with a ⚠ icon and link to the Anomalies panel.

## Testing

```bash
python -m pytest tests/test_anomalies.py -v     # 22 anomaly tests
python -m pytest tests/ -q                       # 118 total
```

Tests cover:
- Config merge + env-var override + invalid env fallback
- Empty tracker returns no anomaly until min_samples
- Z-score detection for both high and low spikes
- Cooldown suppression prevents repeat alerts
- Baseline persistence to disk (survives restart)
- Rollback: spike values drift out of window, mean recovers
- Adapter bus integration: end-to-end publish → detect → publish anomaly event
- Null/non-finite values silently skipped
- Disabled adapter publishes nothing
- All API endpoints respond correctly

## Zero External Dependencies

This module uses only Python stdlib: `math`, `json`, `threading`, `collections.deque`, `pathlib`, `datetime`, `typing`. No numpy, no sklearn, no pandas — suitable for air-gap deployment.

If `anomalies/` is absent (minimal install), the adapter import in `events/adapters/__init__.py` gracefully falls back to `None` and the build server omits it from BUILDERS.
