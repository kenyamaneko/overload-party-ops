"""gcloud / kubectl ラッパと、各コスト発生リソースの稼働確認を提供する。"""
import json
import re
import subprocess

GKE_ZONE = "asia-northeast1-a"
GKE_PROJECT = "keyandnotes-platform"
GKE_CLUSTER = "keyandnotes-main"
CLOUDSQL_INSTANCE = "overload-party-db"
DEPLOYMENTS = ["gateway", "battle", "account", "card", "matchmaking", "shop", "scenario"]

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


def gcloud(*args: str, allow_not_found: bool = False) -> str:
    """gcloud コマンドを JSON 出力で実行します。"""
    return _run_cmd(["gcloud", *args, "--format=json"], label="gcloud", allow_not_found=allow_not_found)


def gcloud_value(*args: str, allow_not_found: bool = False) -> str:
    """gcloud コマンドをテキスト出力で実行します。"""
    return _run_cmd(["gcloud", *args], label="gcloud", allow_not_found=allow_not_found)


def kubectl_json(*args: str, allow_not_found: bool = False) -> str:
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
        state = gcloud_value(
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


def check_gke_deployments(env: str) -> tuple[list[str], list[str]]:
    """GKE Deployment のレプリカ数を確認します。"""
    costs: list[str] = []
    errors: list[str] = []
    for deploy in DEPLOYMENTS:
        try:
            raw = kubectl_json(
                "get", "deployment", deploy,
                "-n", env,
                f"--context=gke_{GKE_PROJECT}_{GKE_ZONE}_{GKE_CLUSTER}",
                allow_not_found=True,
            )
        except CommandError as e:
            errors.append(f"Deployment `{deploy}` チェック失敗: {e}")
            continue
        if not raw:
            # 未デプロイは正常系の一種（コスト発生なし）として扱うが、silent にせず
            # Actions ログに「未デプロイ」を明示する。silent skip すると namespace
            # 内の状態が不可視になり、手動で環境調査する時の判断材料が失われる。
            print(f"  Deployment `{deploy}` は {env} namespace に未デプロイ")
            continue
        try:
            spec = json.loads(raw)
        except json.JSONDecodeError as e:
            errors.append(f"Deployment `{deploy}` JSON パース失敗: {e}")
            continue
        # spec.replicas が「0」と「未設定」を区別する: 0 はスケールダウン済みで正常、
        # 未設定は API レスポンス異常で稼働状態が判定できないためエラー扱い。
        deployment_spec = spec.get("spec", {})
        if "replicas" not in deployment_spec:
            errors.append(f"Deployment `{deploy}` の spec.replicas フィールドが見つかりません")
            continue
        replicas = deployment_spec["replicas"]
        if replicas > 0:
            costs.append(f"Deployment `{deploy}` が {replicas} レプリカ稼働中")
    return costs, errors


def check_ingress(env: str) -> tuple[list[str], list[str]]:
    """Ingress リソースの稼働状態を確認します。"""
    try:
        raw = kubectl_json(
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
        raw = gcloud(
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
        raw = gcloud(
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


def namespace_exists(env: str) -> tuple[bool, str | None]:
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
    env: str, project: str, *, gke_available: bool = True,
) -> tuple[list[str], list[str]]:
    """指定環境のコスト発生リソースを一括チェックします。"""
    costs: list[str] = []
    errors: list[str] = []

    def _collect(result: tuple[list[str], list[str]]) -> None:
        costs.extend(result[0])
        errors.extend(result[1])

    _collect(check_cloudsql(project))
    if gke_available:
        ns_ok, ns_err = namespace_exists(env)
        if ns_err:
            errors.append(ns_err)
        if ns_ok:
            _collect(check_gke_deployments(env))
            _collect(check_ingress(env))
        elif not ns_err:
            print(f"Namespace '{env}' not found, skipping GKE checks.")
    else:
        print("GKE credentials unavailable, skipping GKE checks.")
    _collect(check_static_ips(project))
    _collect(check_psc(project))
    return costs, errors
