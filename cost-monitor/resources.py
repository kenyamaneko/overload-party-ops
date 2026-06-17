"""gcloud / kubectl ラッパと、各コスト発生リソースの稼働確認を提供する。"""
import json
import re
import subprocess

GKE_ZONE = "asia-northeast1-a"
GKE_PROJECT = "keyandnotes-platform"
GKE_CLUSTER = "keyandnotes-main"
CLOUDSQL_INSTANCE = "overload-party-db"

# "リソースが存在しない" エラーのみ抑制するパターン。
# 認証失敗 / quota / network 等はマッチさせず例外で落とす。
_NOT_FOUND_RE = re.compile(
    r"(?:\bNotFound\b"
    r"|\bNOT_FOUND\b"
    r"|\bnot\s+found\b"
    r"|\bcould\s+not\s+be\s+found\b"
    r"|\bdoes\s+not\s+exist\b"
    r"|\b404\b)",
    re.IGNORECASE,
)


class CommandError(Exception):
    pass


def _is_not_found(stderr: str) -> bool:
    """stderr がリソース未存在を示すか判定します。"""
    return bool(_NOT_FOUND_RE.search(stderr))


def format_cmd_failure(stderr: str, stdout: str, returncode: int) -> str:
    """外部コマンド失敗時に stderr/stdout を両方拾った詳細メッセージを返す。

    CLI によっては stderr が空で stdout にしかエラーを吐かないケースがあり
    (Claude CLI 等)、片方だけを見ると silent failure の原因になる。
    subprocess を直接扱う箇所は必ずこのヘルパーを通すこと。
    """
    stderr_s = (stderr or "").strip()
    stdout_s = (stdout or "").strip()
    if stderr_s and stdout_s:
        return f"stderr: {stderr_s}\nstdout: {stdout_s}"
    if stderr_s:
        return stderr_s
    if stdout_s:
        return f"stdout: {stdout_s}"
    return f"exit code {returncode} (stderr/stdout ともに空)"


def _run_cmd(
    cmd: list[str], *, label: str = "cmd", allow_not_found: bool = False,
) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        # `allow_not_found` はリソース未作成のケースのみ許可する。
        # 認証失敗等は not-found パターンにマッチしないため例外で落とす。
        if allow_not_found and stderr and _is_not_found(stderr):
            print(f"[{label}] {stderr}")
            return ""
        # 詳細は print で Actions ログに流し、Slack 向けの例外メッセージは短く保つ
        detail = format_cmd_failure(result.stderr, result.stdout, result.returncode)
        print(f"[{label}] {detail}")
        raise CommandError(f"{label} 実行失敗 (exit {result.returncode})")
    return result.stdout.strip()


def run_gcloud(*args: str, allow_not_found: bool = False) -> str:
    """gcloud コマンドを JSON 出力で実行します。"""
    return _run_cmd(["gcloud", *args, "--format=json"], label="gcloud", allow_not_found=allow_not_found)


def run_gcloud_value(*args: str, allow_not_found: bool = False) -> str:
    """gcloud コマンドをテキスト出力で実行します。"""
    return _run_cmd(["gcloud", *args], label="gcloud", allow_not_found=allow_not_found)


def run_kubectl_json(*args: str, allow_not_found: bool = False) -> str:
    """kubectl コマンドを JSON 出力で実行します。"""
    return _run_cmd(["kubectl", *args, "-o", "json"], label="kubectl", allow_not_found=allow_not_found)


def setup_gke_credentials() -> tuple[bool, str | None]:
    """GKE クラスタの認証情報を取得します。

    成功時: (True, None) / 失敗時: (False, エラー詳細)。
    失敗詳細は呼び出し側で Slack に必ず流すこと。print だけだと Actions
    ログにしか出ず、ユーザーは気付けない。
    """
    result = subprocess.run(
        ["gcloud", "container", "clusters", "get-credentials", GKE_CLUSTER,
         "--zone", GKE_ZONE, "--project", GKE_PROJECT],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        detail = format_cmd_failure(result.stderr, result.stdout, result.returncode)
        print(f"[gcloud] GKE credentials failed: {detail}")
        return False, "GKE 認証失敗 (詳細はログ)"
    return True, None


def check_cloudsql(project: str) -> tuple[list[str], list[str]]:
    """Cloud SQL インスタンスの稼働状態を確認します。"""
    try:
        state = run_gcloud_value(
            "sql", "instances", "describe", CLOUDSQL_INSTANCE,
            "--project", project, "--format=value(state)",
            allow_not_found=True,
        )
    except CommandError as e:
        return [], [f"Cloud SQL チェック失敗: {e}"]
    if not state:
        return [], []
    if state == "RUNNABLE":
        return [f"Cloud SQL `{CLOUDSQL_INSTANCE}` が RUNNABLE ($0.19/hr)"], []
    return [], []


def check_gke_nodepool(env: str) -> tuple[list[str], list[str]]:
    """env 用 GKE node pool の現行ノード数を確認します。"""
    nodepool = f"{GKE_CLUSTER}-{env}"
    try:
        raw = run_gcloud(
            "container", "node-pools", "describe", nodepool,
            "--cluster", GKE_CLUSTER, "--zone", GKE_ZONE, "--project", GKE_PROJECT,
        )
    except CommandError as e:
        return [], [f"Node pool `{nodepool}` チェック失敗: {e}"]
    try:
        nodepool_detail = json.loads(raw)
    except json.JSONDecodeError as e:
        return [], [f"Node pool `{nodepool}` JSON パース失敗: {e}"]
    igs = nodepool_detail.get("instanceGroupUrls", [])
    if not igs:
        return [], [f"Node pool `{nodepool}` の instanceGroupUrls が空"]
    total_target = 0
    for url in igs:
        ig_name = url.rsplit("/", 1)[-1]
        try:
            ig_raw = run_gcloud(
                "compute", "instance-groups", "managed", "describe", ig_name,
                "--zone", GKE_ZONE, "--project", GKE_PROJECT,
            )
        except CommandError as e:
            return [], [f"Instance group `{ig_name}` チェック失敗: {e}"]
        try:
            ig = json.loads(ig_raw)
        except json.JSONDecodeError as e:
            return [], [f"Instance group `{ig_name}` JSON パース失敗: {e}"]
        target = ig.get("targetSize")
        if target is None:
            return [], [f"Instance group `{ig_name}` の targetSize が見つかりません"]
        total_target += target
    if total_target > 0:
        return [f"GKE node pool `{nodepool}` が {total_target} ノード稼働中"], []
    return [], []


def check_ingress(env: str) -> tuple[list[str], list[str]]:
    """Ingress リソースの稼働状態を確認します。"""
    try:
        raw = run_kubectl_json(
            "get", "ingress", "overload-party",
            "-n", env,
            f"--context=gke_{GKE_PROJECT}_{GKE_ZONE}_{GKE_CLUSTER}",
            allow_not_found=True,
        )
    except CommandError as e:
        return [], [f"Ingress チェック失敗: {e}"]
    if not raw:
        return [], []
    try:
        ing = json.loads(raw)
    except json.JSONDecodeError as e:
        return [], [f"Ingress JSON パース失敗: {e}"]
    ip_list = ing.get("status", {}).get("loadBalancer", {}).get("ingress", [])
    if not ip_list:
        return [], []
    # ingress エントリがあるのに ip フィールドが無いのは API レスポンス異常
    ip = ip_list[0].get("ip")
    if ip is None:
        return [], ["Ingress のレスポンスに ip フィールドがありません"]
    return [f"Ingress `overload-party` が稼働中 (IP: {ip}, ~$0.025/hr)"], []


def check_static_ips(project: str) -> tuple[list[str], list[str]]:
    """予約済み外部 IP アドレスを確認します。"""
    try:
        raw = run_gcloud(
            "compute", "addresses", "list",
            "--project", project,
            "--filter", "status=RESERVED AND addressType=EXTERNAL",
        )
    except CommandError as e:
        return [], [f"外部 IP チェック失敗: {e}"]
    try:
        addresses = json.loads(raw) if raw else []
    except json.JSONDecodeError as e:
        return [], [f"外部 IP JSON パース失敗: {e}"]
    costs: list[str] = []
    errors: list[str] = []
    for addr in addresses:
        name = addr.get("name")
        ip = addr.get("address")
        # name/address は GCP API 仕様上必ず返るフィールド。欠落は API 仕様変更
        # かフィルタ条件のミスマッチを示すので silent に "unknown" 表示せず errors に流す
        if name is None or ip is None:
            errors.append(f"外部 IP のレスポンスに name/address がありません: {addr}")
            continue
        costs.append(f"予約済み外部 IP `{name}` ({ip}, ~$3.65/mo)")
    return costs, errors


def check_psc(project: str) -> tuple[list[str], list[str]]:
    """PSC forwarding rule の稼働状態を確認します。"""
    try:
        raw = run_gcloud(
            "compute", "forwarding-rules", "list",
            "--project", project,
            "--filter", "target~serviceAttachments",
        )
    except CommandError as e:
        return [], [f"PSC チェック失敗: {e}"]
    try:
        rules = json.loads(raw) if raw else []
    except json.JSONDecodeError as e:
        return [], [f"PSC JSON パース失敗: {e}"]
    costs: list[str] = []
    errors: list[str] = []
    for rule in rules:
        name = rule.get("name")
        if name is None:
            errors.append(f"PSC forwarding rule のレスポンスに name がありません: {rule}")
            continue
        costs.append(f"PSC forwarding rule `{name}` が稼働中")
    return costs, errors


def is_namespace_present(env: str) -> tuple[bool, str | None]:
    """Kubernetes namespace が存在するか確認します。

    戻り値: (存在するか, エラー詳細)。NotFound は (False, None)、
    それ以外 (認証失敗/RBAC 等) は (False, エラー詳細) を返し、呼び出し側で
    Slack に流すこと。
    """
    result = subprocess.run(
        ["kubectl", "get", "namespace", env,
         f"--context=gke_{GKE_PROJECT}_{GKE_ZONE}_{GKE_CLUSTER}"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return True, None
    combined = f"{result.stderr}\n{result.stdout}"
    if _is_not_found(combined):
        return False, None
    detail = format_cmd_failure(result.stderr, result.stdout, result.returncode)
    print(f"[kubectl] namespace check failed: {detail}")
    return False, f"Namespace `{env}` 確認失敗 (詳細はログ)"


def check_environment(
    env: str, project: str, *, is_gke_available: bool = True,
) -> tuple[list[str], list[str]]:
    """指定環境のコスト発生リソースを一括チェックします。"""
    costs: list[str] = []
    errors: list[str] = []

    def _collect(result: tuple[list[str], list[str]]) -> None:
        costs.extend(result[0])
        errors.extend(result[1])

    _collect(check_cloudsql(project))
    _collect(check_gke_nodepool(env))
    if is_gke_available:
        is_namespace_ok, ns_err = is_namespace_present(env)
        if ns_err:
            errors.append(ns_err)
        if is_namespace_ok:
            _collect(check_ingress(env))
        elif not ns_err:
            print(f"Namespace '{env}' not found, skipping Ingress check.")
    else:
        print("GKE credentials unavailable, skipping Ingress check.")
    _collect(check_static_ips(project))
    _collect(check_psc(project))
    return costs, errors
