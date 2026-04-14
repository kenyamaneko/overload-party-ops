#!/usr/bin/env bash
set +e

NS="overload-party-${ENV}"
# dev / stg が共有する nodepool。ADR-018 で Pod scale 0 方式から
# nodepool 0 ノード resize 方式に切り替えており、VM 課金はここを 0 に
# することで止まる。dev と stg の shutdown は同じ pool を落とすため、
# 片方の shutdown がもう片方の Pod も停止させる（運用上許容）。
NODEPOOL_DEV_STG="keyandnotes-main-dev"
HAS_ERROR=false
RESULTS=""
INGRESS_DELETED=false
add_result() { RESULTS="${RESULTS}• $1\n"; }
mark_error() { HAS_ERROR=true; }

# NotFound 系メッセージを判定する。認証/権限エラーと「リソース不在」を
# 区別するため、明確に「存在しない」を示す文言だけを通し、それ以外は
# エラー扱いにして詳細を Actions ログに流す。
is_not_found() {
  echo "$1" | grep -qiE 'not[ -]?found|does not exist|was not found|no[ -]?such'
}

# 外部コマンドの失敗詳細は Slack に詰めず stdout(Actions ログ) に吐く。
# Slack 通知 (notify.sh) は `<...|ログ>` リンクを必ず付けるので、ユーザーは
# ここから失敗原因を辿れる。
log_cmd_failure() {
  local label="$1" output="$2"
  echo "[ERROR] ${label}: ${output}"
}

# ─── GKE 認証失敗の詳細取得 ───
# workflow 側の get-gke-credentials は continue-on-error のため stderr が
# Slack に流れない。失敗時はここで認証を再試行して stderr を Actions ログへ。
if [ "${GKE_AUTH_OK}" != "true" ]; then
  AUTH_OUT=$(gcloud container clusters get-credentials "${GKE_CLUSTER}" \
    --region "${GKE_REGION}" --project "${GKE_PROJECT}" 2>&1)
  log_cmd_failure "GKE 認証失敗" "${AUTH_OUT}"
  add_result "GKE 認証失敗 (詳細はログ)"
  mark_error
fi

# ─── Namespace 存在確認 ───
NS_EXISTS=false
if [ "${GKE_AUTH_OK}" = "true" ]; then
  NS_OUT=$(kubectl get namespace "${NS}" 2>&1)
  NS_RC=$?
  if [ ${NS_RC} -eq 0 ]; then
    NS_EXISTS=true
  elif is_not_found "${NS_OUT}"; then
    :
  else
    log_cmd_failure "Namespace 確認失敗" "${NS_OUT}"
    add_result "Namespace 確認失敗 (詳細はログ)"; mark_error
  fi
fi

# ─── Ingress / BackendConfig 削除 ───
if [ "${GKE_AUTH_OK}" != "true" ]; then
  add_result "Ingress: GKE 認証失敗のためスキップ"
elif [ "${NS_EXISTS}" != "true" ]; then
  add_result "Ingress: namespace 不在のためスキップ"
else
  ING_OUT=$(kubectl get ingress overload-party -n "${NS}" 2>&1)
  ING_RC=$?
  if [ ${ING_RC} -ne 0 ] && is_not_found "${ING_OUT}"; then
    add_result "Ingress: 元から存在しない"
  elif [ ${ING_RC} -ne 0 ]; then
    log_cmd_failure "Ingress 確認失敗" "${ING_OUT}"
    add_result "Ingress: 確認失敗 (詳細はログ)"; mark_error
  else
    ING_DEL_OUT=$(kubectl delete ingress overload-party -n "${NS}" 2>&1)
    ING_DEL_RC=$?
    BC_DEL_OUT=$(kubectl delete backendconfig battle-backend-config -n "${NS}" --ignore-not-found 2>&1)
    BC_DEL_RC=$?
    if [ ${ING_DEL_RC} -eq 0 ] && [ ${BC_DEL_RC} -eq 0 ]; then
      add_result "Ingress: 削除"
      INGRESS_DELETED=true
    else
      [ ${ING_DEL_RC} -ne 0 ] && log_cmd_failure "Ingress 削除失敗" "${ING_DEL_OUT}"
      [ ${BC_DEL_RC} -ne 0 ] && log_cmd_failure "BackendConfig 削除失敗" "${BC_DEL_OUT}"
      add_result "Ingress: 削除失敗 (詳細はログ)"; mark_error
    fi
  fi
fi

# ─── DNS ───
if [ "${ENV}" = "dev" ]; then
  RECORD_ID="${CLOUDFLARE_DNS_RECORD_ID_DEV}"
else
  RECORD_ID="${CLOUDFLARE_DNS_RECORD_ID_STG}"
fi

RESPONSE=$(curl -s -X PATCH \
  "https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/dns_records/${RECORD_ID}" \
  -H "Authorization: Bearer ${CLOUDFLARE_DNS_API_TOKEN}" \
  -H "Content-Type: application/json" \
  --data '{"content": "127.0.0.1", "proxied": false}')

if echo "${RESPONSE}" | jq -e '.success' >/dev/null 2>&1; then
  add_result "DNS: 127.0.0.1 に変更"
else
  log_cmd_failure "DNS 変更失敗" "${RESPONSE}"
  add_result "DNS: 変更失敗 (詳細はログ)"; mark_error
fi

# ─── 予約 IP 削除 ───
# Ingress 削除直後は LB 参照が残り IP が IN_USE のままになるため、
# 実削除の直前にだけ LB cleanup を待つ。
if [ "${INGRESS_DELETED}" = "true" ]; then
  echo "Waiting for LB cleanup..."
  sleep 60
fi

IP_LIST=$(gcloud compute addresses list \
  --filter="status=RESERVED AND addressType=EXTERNAL" \
  --global --format="value(name)" \
  --project="${GKE_PROJECT}" 2>&1)
IP_LIST_RC=$?
if [ ${IP_LIST_RC} -ne 0 ]; then
  log_cmd_failure "予約 IP 取得失敗" "${IP_LIST}"
  add_result "予約 IP: 取得失敗 (詳細はログ)"; mark_error
  IP_LIST=""
fi

if [ -z "${IP_LIST}" ]; then
  add_result "予約 IP: なし"
else
  DELETED=""
  IN_USE=""
  FAILED=""
  # in-use の IP は課金が継続するため failure として Slack / exit code に反映する
  for IP_NAME in ${IP_LIST}; do
    DELETE_OUTPUT=$(gcloud compute addresses delete "${IP_NAME}" \
      --global --project="${GKE_PROJECT}" --quiet 2>&1)
    DELETE_RC=$?
    if [ ${DELETE_RC} -eq 0 ]; then
      DELETED="${DELETED:+${DELETED}, }${IP_NAME}"
    elif echo "${DELETE_OUTPUT}" | grep -qiE 'in[- ]?use|resourceInUse'; then
      IN_USE="${IN_USE:+${IN_USE}, }${IP_NAME}"
    else
      log_cmd_failure "予約 IP ${IP_NAME} 削除失敗" "${DELETE_OUTPUT}"
      FAILED="${FAILED:+${FAILED}, }${IP_NAME}"
    fi
  done
  MSG="予約 IP: "
  [ -n "${DELETED}" ] && MSG="${MSG}${DELETED} を削除"
  if [ -n "${IN_USE}" ]; then
    [ -n "${DELETED}" ] && MSG="${MSG} / "
    MSG="${MSG}${IN_USE} は使用中 (課金継続中)"
    mark_error
  fi
  if [ -n "${FAILED}" ]; then
    { [ -n "${DELETED}" ] || [ -n "${IN_USE}" ]; } && MSG="${MSG} / "
    MSG="${MSG}${FAILED} 削除失敗 (詳細はログ)"
    mark_error
  fi
  add_result "${MSG}"
fi

# ─── PSC ───
PSC_RULE="cloudsql-psc-${ENV}"
PSC_DESC_OUT=$(gcloud compute forwarding-rules describe "${PSC_RULE}" \
  --project="${GKE_PROJECT}" --region="${GKE_REGION}" 2>&1)
PSC_DESC_RC=$?
if [ ${PSC_DESC_RC} -ne 0 ] && is_not_found "${PSC_DESC_OUT}"; then
  add_result "PSC: 元から存在しない"
elif [ ${PSC_DESC_RC} -ne 0 ]; then
  log_cmd_failure "PSC 確認失敗" "${PSC_DESC_OUT}"
  add_result "PSC: 確認失敗 (詳細はログ)"; mark_error
else
  PSC_DEL_OUT=$(gcloud compute forwarding-rules delete "${PSC_RULE}" \
    --project="${GKE_PROJECT}" --region="${GKE_REGION}" --quiet 2>&1)
  if [ $? -eq 0 ]; then
    add_result "PSC: ${PSC_RULE} を削除"
  else
    log_cmd_failure "PSC ${PSC_RULE} 削除失敗" "${PSC_DEL_OUT}"
    add_result "PSC: ${PSC_RULE} の削除失敗 (詳細はログ)"; mark_error
  fi
fi

# ─── Nodepool を 0 ノードに resize ───
# ADR-018 決定により Pod scale 0 方式から nodepool resize 方式へ変更。
# Standard モードでは Pod を 0 にしても VM 課金は止まらないため、
# nodepool 自体を 0 ノードにすることで VM 課金を止める。
# dev と stg は同一 nodepool を共有しているので、どちらの ENV で
# shutdown しても同じ pool を 0 にする（冪等）。
RESIZE_OUT=$(gcloud container clusters resize "${GKE_CLUSTER}" \
  --node-pool="${NODEPOOL_DEV_STG}" \
  --num-nodes=0 \
  --region="${GKE_REGION}" \
  --project="${GKE_PROJECT}" \
  --quiet 2>&1)
if [ $? -eq 0 ]; then
  add_result "Nodepool: ${NODEPOOL_DEV_STG} を 0 ノードに resize (dev/stg 共有)"
else
  log_cmd_failure "Nodepool resize 失敗" "${RESIZE_OUT}"
  add_result "Nodepool: ${NODEPOOL_DEV_STG} resize 失敗 (詳細はログ)"; mark_error
fi

# ─── Cloud SQL ───
CLOUDSQL_PROJECT="overload-party-${ENV}"
CLOUDSQL_INSTANCE="overload-party-db"
SQL_DESC_OUT=$(gcloud sql instances describe "${CLOUDSQL_INSTANCE}" \
  --project="${CLOUDSQL_PROJECT}" \
  --format="value(settings.activationPolicy)" 2>&1)
SQL_DESC_RC=$?

if [ ${SQL_DESC_RC} -ne 0 ] && is_not_found "${SQL_DESC_OUT}"; then
  add_result "Cloud SQL: インスタンス未作成のためスキップ"
elif [ ${SQL_DESC_RC} -ne 0 ]; then
  log_cmd_failure "Cloud SQL 確認失敗" "${SQL_DESC_OUT}"
  add_result "Cloud SQL: 確認失敗 (詳細はログ)"; mark_error
elif [ "${SQL_DESC_OUT}" = "NEVER" ]; then
  add_result "Cloud SQL: 既に停止済み"
else
  SQL_PATCH_OUT=$(gcloud sql instances patch "${CLOUDSQL_INSTANCE}" \
    --activation-policy=NEVER \
    --project="${CLOUDSQL_PROJECT}" --quiet 2>&1)
  if [ $? -eq 0 ]; then
    add_result "Cloud SQL: 停止"
  else
    log_cmd_failure "Cloud SQL 停止失敗" "${SQL_PATCH_OUT}"
    add_result "Cloud SQL: 停止失敗 (詳細はログ)"; mark_error
  fi
fi

# ─── 結果出力 ───
{
  echo "results<<EOF"
  echo -e "${RESULTS}"
  echo "EOF"
  echo "has_error=${HAS_ERROR}"
} >> "$GITHUB_OUTPUT"
