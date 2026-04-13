#!/usr/bin/env bash
set +e

NS="overload-party-${ENV}"
HAS_ERROR=false
RESULTS=""
INGRESS_DELETED=false
add_result() { RESULTS="${RESULTS}• $1\n"; }
mark_error() { HAS_ERROR=true; }

# ─── Namespace 存在確認 ───
NS_EXISTS=false
if [ "${GKE_AUTH_OK}" = "true" ]; then
  kubectl get namespace "${NS}" &>/dev/null && NS_EXISTS=true
fi

# ─── Ingress / BackendConfig 削除 ───
if [ "${GKE_AUTH_OK}" != "true" ]; then
  add_result "Ingress: GKE 認証失敗のためスキップ"
elif [ "${NS_EXISTS}" != "true" ]; then
  add_result "Ingress: namespace 不在のためスキップ"
elif ! kubectl get ingress overload-party -n "${NS}" &>/dev/null; then
  add_result "Ingress: 元から存在しない"
elif kubectl delete ingress overload-party -n "${NS}" && \
     kubectl delete backendconfig battle-backend-config -n "${NS}" --ignore-not-found; then
  add_result "Ingress: 削除"
  INGRESS_DELETED=true
else
  add_result "Ingress: 削除失敗"; mark_error
fi

# ─── 予約 IP 削除 ───
if [ "${INGRESS_DELETED}" = "true" ]; then
  echo "Waiting for LB cleanup..."
  sleep 60
fi

if ! IP_LIST=$(gcloud compute addresses list \
  --filter="status=RESERVED AND addressType=EXTERNAL" \
  --global --format="value(name)" \
  --project="${GKE_PROJECT}" 2>&1); then
  add_result "予約 IP: 取得失敗 (${IP_LIST})"; mark_error
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
    MSG="${MSG}${FAILED} 削除失敗"
    mark_error
  fi
  add_result "${MSG}"
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

if echo "${RESPONSE}" | jq -e '.success' &>/dev/null; then
  add_result "DNS: 127.0.0.1 に変更"
else
  ERROR=$(echo "${RESPONSE}" | jq -r '.errors[0].message // "unknown"' 2>/dev/null || echo "unknown")
  add_result "DNS: 変更失敗 (${ERROR})"; mark_error
fi

# ─── Pod スケールダウン ───
# newsfeed は Cloud Run Job なので対象外
if [ "${GKE_AUTH_OK}" != "true" ]; then
  add_result "Pod: GKE 認証失敗のためスキップ"
elif [ "${NS_EXISTS}" != "true" ]; then
  add_result "Pod: namespace 不在のためスキップ"
else
  POD_PARTS=""
  for DEPLOY in gateway battle account card matchmaking shop scenario; do
    if ! kubectl get deployment "${DEPLOY}" -n "${NS}" &>/dev/null; then
      POD_PARTS="${POD_PARTS}${DEPLOY} 存在しない, "
    elif kubectl scale deployment "${DEPLOY}" --replicas=0 -n "${NS}" &>/dev/null; then
      POD_PARTS="${POD_PARTS}${DEPLOY} → 0, "
    else
      POD_PARTS="${POD_PARTS}${DEPLOY} スケール失敗, "; mark_error
    fi
  done
  add_result "Pod: ${POD_PARTS%, }"
fi

# ─── PSC ───
PSC_RULE="cloudsql-psc-${ENV}"
if ! gcloud compute forwarding-rules describe "${PSC_RULE}" \
       --project="${GKE_PROJECT}" --region="${GKE_REGION}" &>/dev/null; then
  add_result "PSC: 元から存在しない"
elif gcloud compute forwarding-rules delete "${PSC_RULE}" \
       --project="${GKE_PROJECT}" --region="${GKE_REGION}" --quiet 2>&1; then
  add_result "PSC: ${PSC_RULE} を削除"
else
  add_result "PSC: ${PSC_RULE} の削除失敗"; mark_error
fi

# ─── Cloud SQL ───
CLOUDSQL_PROJECT="overload-party-${ENV}"
CLOUDSQL_INSTANCE="overload-party-db"
CURRENT_POLICY=$(gcloud sql instances describe "${CLOUDSQL_INSTANCE}" \
  --project="${CLOUDSQL_PROJECT}" \
  --format="value(settings.activationPolicy)" 2>/dev/null)

if [ -z "${CURRENT_POLICY}" ]; then
  add_result "Cloud SQL: インスタンス未作成のためスキップ"
elif [ "${CURRENT_POLICY}" = "NEVER" ]; then
  add_result "Cloud SQL: 既に停止済み"
elif gcloud sql instances patch "${CLOUDSQL_INSTANCE}" \
       --activation-policy=NEVER \
       --project="${CLOUDSQL_PROJECT}" --quiet 2>&1; then
  add_result "Cloud SQL: 停止"
else
  add_result "Cloud SQL: 停止失敗"; mark_error
fi

# ─── 結果出力 ───
{
  echo "results<<EOF"
  echo -e "${RESULTS}"
  echo "EOF"
  echo "has_error=${HAS_ERROR}"
} >> "$GITHUB_OUTPUT"
