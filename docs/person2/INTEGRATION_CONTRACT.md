# ASTRA Person 2 Integration Contract

## Ownership
Person 2 owns quantitative feature engineering, model training/evaluation, evidence scoring, hypothesis comparison, and falsification.

## Recommended shared objects
- Transaction: `txId`, `time_step`, `from_address`/source node, `to_address`/target node, `value`, `timestamp`, `chain_id`, `source`
- Evidence: `evidence_id`, `kind`, `direction`, `reliability`, `strength`, `source`, `description`, `collected_at`, `source_timestamp`, `verified`
- Hypothesis: `hypothesis_id`, `title`, `evidence[]`
- Model prediction: `entity_id`, `score`, `model_version`, `feature_version`, `limitations`

Elliptic++ transaction IDs are dataset identifiers, not real Bitcoin addresses. Do not pretend they are Ethereum addresses or use them as wallet identifiers in ASTRA's Ethereum demo.

## API
- `GET /health`
- `GET /model/info`
- `POST /score/evidence`
- `POST /hypotheses/compare`
- `POST /falsify`

Use `uvicorn astra_intel.api:app --reload` from the package root.

## Integration caution
The heuristic case score and the trained model's illicit-pattern score are different quantities. Keep their names, calibration, and interpretations separate. Do not combine them into one number without a documented, evaluated fusion method.
