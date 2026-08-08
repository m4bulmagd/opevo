# Dependency Security Exception Register

Dependency audit exceptions are release-blocking security decisions, not a way
to make a scanner green. Each entry must identify an exact advisory, prove why
the vulnerable path is unreachable, state compensating controls, and carry a
short review deadline. Delete an exception as soon as the dependency graph can
take a fixed version.

## Agent `transformers==4.57.1`

- **Status:** temporarily accepted for the beta
- **Owner:** Presvo engineering
- **Accepted:** 2026-07-14
- **Last reviewed:** 2026-08-08
- **Review by:** 2026-08-12
- **Expires:** 2026-08-14
- **Affected application:** `apps/agent` only
- **Dependency paths:**
  - `livekit-plugins-turn-detector==1.6.9` requires
    `transformers>=4.47.1,<=4.57.1`.
  - `livekit-plugins-speechmatics==1.6.9` installs
    `speechmatics-voice[smart]==0.2.8`, which requires
    `transformers>=4.57,<5`.

The following exact audit findings are accepted until either upstream path can
resolve a fixed `transformers` version:

| pip-audit ID | Related advisory | Vulnerable behavior | Why this agent does not reach it |
| --- | --- | --- | --- |
| `PYSEC-2025-217` | `CVE-2025-14929` | X-CLIP checkpoint deserialization | The agent never loads X-CLIP models or caller-supplied checkpoints. |
| `PYSEC-2025-218` | `CVE-2025-14930` | GLM4 weight deserialization | The agent never loads GLM4 models or caller-supplied weights. |
| `PYSEC-2026-2290` | `GHSA-fgcw-684q-jj6r` / `CVE-2026-5241` | LightGlue remote-code loading | The agent never loads LightGlue or an externally selected model repository. |
| `PYSEC-2026-2288` | `GHSA-69w3-r845-3855` / `CVE-2026-1839` | `Trainer` checkpoint deserialization | The production agent does inference only and never imports or invokes `Trainer`. |
| `PYSEC-2026-2289` | `GHSA-29pf-2h5f-8g72` / `CVE-2026-4372` | Malicious `AutoModel` configuration loading | The agent never calls an `AutoModel` API or accepts a model identifier from users or dispatch metadata. |

As of the 2026-08-08 review, the advisory service returns
`PYSEC-2026-2290` twice with the same `GHSA-fgcw-684q-jj6r` and
`CVE-2026-5241` aliases: one row has no fix version and one names `5.5.0`.
This is six scanner rows for the five distinct reviewed advisory IDs above,
not a sixth exception. CI deliberately checks that exact row count so a new
identifier still fails the audit.

### Compensating controls and invalidation conditions

- The LiveKit turn detector is fixed to the `livekit/turn-detector` repository
  and plugin-owned immutable revisions. Runtime initialization uses local files
  only and invokes `AutoTokenizer`, not an `AutoModel` or `Trainer` path.
- Speechmatics smart-turn uses `WhisperFeatureExtractor` with a fixed ONNX model
  URL; it does not deserialize a Transformers model. The default production
  mode is `adaptive`, not `smart_turn`.
- Provider and pipeline tests ensure dispatch metadata selects only the
  supported speech providers; it cannot supply model repository IDs, local
  checkpoint paths, or arbitrary Transformers configuration.
- The lockfile pins the affected graph, Dependabot checks the two upstream
  plugins weekly, and CI continues auditing every other package and advisory.
- Trivy's image scan suppresses only `CVE-2026-4372` and `CVE-2026-5241`, only
  for the exact `transformers==4.57.1` metadata path and package URL, and only
  until this entry's expiration date. Every other fixed HIGH/CRITICAL image
  finding remains release-blocking.

This exception becomes invalid immediately if application code begins loading
user-controlled models, repositories, configuration, weights, or checkpoints;
if it begins using `Trainer`, an `AutoModel` API, X-CLIP, GLM4, or LightGlue; or
if the relevant model locations become controllable through dispatch metadata.
Such a change must be blocked until the dependency can be upgraded or the
model-loading operation is isolated in a separately sandboxed service.

### Exact local audit

```bash
cd apps/agent
UV_CACHE_DIR=/tmp/uv-cache uv export --frozen --all-groups --no-emit-project --no-emit-local \
  --format requirements-txt --output-file /tmp/presvo-agent-requirements.txt
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync pip-audit \
  --disable-pip --require-hashes --no-deps --progress-spinner=off \
  --requirement /tmp/presvo-agent-requirements.txt \
  --ignore-vuln PYSEC-2025-217 \
  --ignore-vuln PYSEC-2025-218 \
  --ignore-vuln PYSEC-2026-2290 \
  --ignore-vuln PYSEC-2026-2288 \
  --ignore-vuln PYSEC-2026-2289
```

The expected result is zero unignored vulnerabilities plus six explicitly
skipped rows representing the five distinct IDs above. A new advisory or a
different skipped count fails the audit and requires a new review; it must not
be added to this list automatically.
