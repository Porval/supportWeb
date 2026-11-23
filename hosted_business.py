"""Wire the hosted workspace to the .49 business implementation and its optional sources."""
from contextlib import ExitStack
import fcntl
import hashlib
import os
from pathlib import Path
import sys


def run_sales_worker():
    """Load the packaged time zone and operating-system trust store before any sales code."""
    tzdir = Path(__file__).resolve().parent / "zoneinfo"
    shanghai = tzdir / "Asia" / "Shanghai"
    if shanghai.is_file():
        if shanghai.resolve() != shanghai:
            raise RuntimeError("The packaged Shanghai time zone must be a regular release file")
        import zoneinfo
        zoneinfo.reset_tzpath((str(tzdir),))
    if sys.platform.startswith("linux"):
        for ca in ("/etc/pki/tls/certs/ca-bundle.crt", "/etc/ssl/certs/ca-certificates.crt",
                   "/etc/ssl/ca-bundle.pem"):
            if Path(ca).is_file() and os.access(ca, os.R_OK):
                os.environ["SSL_CERT_FILE"] = ca
                break
    # ModelWorker.invoke sets cwd to the verified packaged sales worker directory.
    sys.path.insert(0, str(Path.cwd().parents[1]))
    from business_agent.activity_sales_comparison.worker import main
    main()


if __name__ == "__main__":
    run_sales_worker()
    raise SystemExit(0)

from account_primitives import APIError, iso
import b_api
from b_jobs import Jobs
from b_model_intake import ModelIntakeWorker
from b_model_recognition import ModelRecognition, ProviderFailure
from b_platform_materials import PlatformMaterials
from b_primary_recognition import PrimaryRecognition
from b_product_metrics import AgentProductMetrics
from b_product_recognition import ProductRecognition
from b_sales_worker import SalesWorker
from b_state import canon
from b_supply_prices import AgentSupplyPrices
from business_agent.supply_price_evidence import adapter as supply
from hosted_state import exact_path
import hosted_agent_bridge as agent_http


class HostedIntake(ModelIntakeWorker):
    def available(self):
        return bool(os.environ.get("JIATAN_ACTIVITY_AGENT_PORT")
                    and self.pinned() and agent_http.agent_ready(require_token=True))

    def invoke(self, args, payload, stop, deadline):
        # This distribution only uses the co-launched Agent. Unset or stale configuration
        # must never redirect submitted materials to another worker, socket, or public URL.
        if args:
            raise ProviderFailure("INPUT_INVALID")
        try:
            return self.decode(agent_http.analyze(payload, stop, deadline))
        except agent_http.BridgeError as exc:
            raise ProviderFailure(exc.code) from None


class HostedSales(SalesWorker):
    module = "hosted_business"

    def import_root(self):
        return Path(__file__).resolve().parent

    def invoke(self, args, payload, stop, deadline):
        # The upstream query creates a ten-second process deadline. The HTTPS adapter
        # accepts up to 60 seconds, so reserve 65 seconds including child overhead.
        self.remaining(stop, deadline)
        return super().invoke(args, payload, stop, deadline + 55)

    def query(self, operation, request, today):
        if not Path(self.settings).exists():
            return SalesWorker(self.directory, None).query(operation, request, today)
        return super().query(operation, request, today)


def record_preflight_failures(domain):
    """Keep each authorized, consented recognition attempt visible in the submission history."""
    original = domain.start_analysis

    def start(db, actor, submission_id, body):
        try:
            return original(db, actor, submission_id, body)
        except APIError as error:
            messages = {
                "AGENT_MODEL_AUTH_REJECTED": "Agent 鉴权失败，原材料已保存。更新识别配置后可重新识别。",
                "AGENT_MODEL_UNAVAILABLE": "识别 Agent 未配置或暂不可用。原材料已保存，可在恢复后重新识别。",
            }
            if error.code not in messages:
                raise
            code = error.code
        # The upstream preflight already checked ownership, consent, input revision, prior
        # result, and active runs. Reuse its reservation, command idempotency and response shape.
        supplement = {"_modelFirst": {"consent": True, "consentBy": actor["id"],
                        "consentedAt": iso(domain.clock()), "inputRevision": body["expectedRevision"]},
                      "agentRecognition": {"contractVersion": "activity-model-intake/1.0", "provider": "deepseek",
                        "status": "failed", "reasonCode": code, "model": None, "promptVersion": None}}
        sub = db.execute("SELECT * FROM b_submissions WHERE id=?", (submission_id,)).fetchone()
        result = domain.queue_analysis(db, sub, supplement)
        try:
            failure = {"code": code, "message": messages[code]}
            payload = dict(supplement, phase="failed", error=failure, result=None, materialResults=[])
            db.execute("UPDATE analysis_runs SET state='failed',payload=?,finished_at=? WHERE id=?",
                       (canon(payload), domain.clock(), result["runId"]))
            domain.jobs.failure_state(db, submission_id, sub["revision"], "failed", False)
            # Domain.command schedules the reserved run after commit. Jobs.run sees its
            # terminal state and releases the reservation without invoking any worker.
            return 200, dict(result, state="failed", error=failure)
        except BaseException:
            domain.jobs.release_reservation(result["runId"])
            raise

    domain.start_analysis = start


def build_business(accounts, clock, decode_material):
    """Return the live Business. Call business.close() when the serving process stops."""
    data = accounts.paths.directory
    agents = Path(b_api.__file__).resolve().parent / "business_agent"
    integrations = data / "integrations"

    def config(variable, relative):
        return exact_path(os.environ.get(variable) or integrations / relative)

    sales_settings = config("JIATAN_ACTIVITY_SALES_SETTINGS", "sales/settings.json")

    resources = ExitStack()
    try:
        # Recovery updates interrupted jobs. A second serving process must never run it
        # against an active worker, even during an overlapping release attempt.
        lock = os.open(data, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        resources.callback(os.close, lock)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # The published Agent serves its model-first contract. The legacy suggestion
        # contracts can read their stored receipts but have no external provider here.
        models = ModelRecognition(accounts, clock, None)
        resources.callback(models.close)
        primary = PrimaryRecognition(accounts, clock, None)
        resources.callback(primary.close)
        intake = HostedIntake(agents / "activity_model_first", integrations / "agent-provider/settings.json")
        jobs = Jobs(accounts, clock, model_agent=intake, local_only=False)
        resources.callback(jobs.close)
        platform = PlatformMaterials(accounts, clock, decode_material, "/api/test/v1/operations")
        business = b_api.Business(
            accounts, jobs, clock, require_activity_name=True, allow_submission_delete=True,
            allow_submission_declarations=True, first_review_start=True, platform_materials=platform,
            model_recognition=models, primary_recognition=primary, activity_date_decoder=decode_material,
            sales_worker=HostedSales(agents / "activity_sales_comparison", sales_settings),
        )
        record_preflight_failures(business.domain)
        products = ProductRecognition(config("JIATAN_UPC_SOURCE", "products/product-upc.json"),
                                      data / "product-recognition")
        business.product_recognition = products
        business.supply_prices = AgentSupplyPrices(
            config("JIATAN_SUPPLY_SOURCE", "products/supply.xls"), supply.load_export,
            supply.lookup_supply_prices, hashlib.sha256(Path(supply.__file__).read_bytes()).hexdigest(),
        )
        business.product_metrics = AgentProductMetrics(
            config("JIATAN_METRICS_SOURCE", "metrics/meituan-sales.json"),
            config("JIATAN_METRICS_BINDING", "metrics/meituan-sales-binding.json"), clock,
        )
        resources.callback(products.close)
        products.start()
        business.close = resources.close
        return business
    except BaseException:
        resources.close()
        raise
