#!/bin/bash
# Full-size v3 experiment. Generates the v3 fleets with the same sizes and seeds as v2, checks their
# realism, trains the real pipeline on them in a SCRATCH folder, scores the rack-level rule, and
# compares with production. Production (models/, the v2 data) is never written; its hashes are
# checked at the end. Outputs go to build/v3/ (git-ignored).
#
# Run it as a transient unit, so it survives closing the terminal and stays under the memory cap
# the README documents for full-size training:
#   systemd-run --unit=v3-experiment -p MemoryMax=11G -p MemorySwapMax=0 \
#       --working-directory="$PWD" /bin/bash scripts/v3_experiment.sh
#   tail -f build/v3/experiment.log
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-$PWD/venv/bin/python3}"
[ -x "$PY" ] || PY=python3
OUT=build/v3
mkdir -p "$OUT"
exec > >(tee -a "$OUT/experiment.log") 2>&1
step() { echo; echo "[$(date +%T)] == $* =="; }
G=ml-model/generate_telecom_fleet_v3.py

free_gb=$(df -BG --output=avail data | tail -1 | tr -dc 0-9)
if [ "$free_gb" -lt 8 ]; then echo "need at least 8 GB free in data/ (have ${free_gb} GB)"; exit 1; fi

gen() {   # days seed file: generated once, kept on reruns
  if [ -s "data/$3" ] && [ -s "data/${3%.csv}.incidents.json" ]; then
    echo "keeping data/$3"
  else
    "$PY" "$G" --machines 200 --days "$1" --seed "$2" --output "data/$3"
  fi
}

step "1/5 generate the v3 fleets (same sizes and seeds as v2)"
gen 30 42   telecom_fleet_v3_labeled.csv
gen 14 7    telecom_fleet_v3_val.csv
gen 30 123  telecom_fleet_v3_test.csv
gen 3  2026 telecom_fleet_v3_operator.csv
wc -l data/telecom_fleet_v3_*.csv

step "2/5 realism of the training fleet"
"$PY" scripts/failure_model_report.py --data data/telecom_fleet_v3_labeled.csv | tee "$OUT/realism_report.txt"
"$PY" scripts/cascade_check.py --data data/telecom_fleet_v3_labeled.csv | tee "$OUT/cascade_check.txt"

step "3/5 train on v3 in a scratch folder (production untouched)"
sha256sum models/*.pkl models/manifest.json > "$OUT/production_before.sha256"
rm -rf "$OUT/models" "$OUT/candidate"
mkdir -p "$OUT/models"
cp models/telecom_xgb_classifier_v2.pkl models/telecom_xgb_label_encoder_v2.pkl \
   models/telecom_xgb_v4_rolling.pkl models/telecom_xgb_v4_rolling_encoder.pkl \
   models/telecom_iso_v2.pkl models/telecom_baselines_v2.json models/manifest.json "$OUT/models/"
set +e
env -u MLFLOW_TRACKING_URI "$PY" ml-model/train_production.py \
    --train data/telecom_fleet_v3_labeled.csv --test data/telecom_fleet_v3_test.csv \
    --val data/telecom_fleet_v3_val.csv --promote \
    --models-dir "$OUT/models" --work-dir "$OUT/candidate" --report "$OUT/report.json"
rc=$?
set -e
echo "training exit code $rc (0 guardrail passed, 1 guardrail rejected the candidate, 2 failed)"
if [ ! -s "$OUT/report.json" ]; then echo "no report written: stopping"; exit 1; fi
THR=$("$PY" -c "import json; print(json.load(open('$OUT/report.json'))['params']['threshold'])")

step "4/5 rack-level rule on the v3 test fleet (candidate at $THR, production at its own threshold)"
"$PY" scripts/rack_rule_eval.py --data data/telecom_fleet_v3_test.csv --models "$OUT/candidate" \
    --threshold "$THR" | grep -v "scored machines" | tee "$OUT/rack_candidate.txt"
"$PY" scripts/rack_rule_eval.py --data data/telecom_fleet_v3_test.csv --models models \
    | grep -v "scored machines" | tee "$OUT/rack_production.txt"

step "5/5 compare"
"$PY" scripts/compare_v2_v3.py --report "$OUT/report.json" | tee "$OUT/comparison.txt"
if sha256sum -c --quiet "$OUT/production_before.sha256"; then
  echo "production artifacts unchanged"
else
  echo "WARNING: production artifacts changed"
fi
echo "[$(date +%T)] DONE"
