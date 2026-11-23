"""Runtime paths and integrity checks for the published subset of the .49 distribution."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
RELEASE = ROOT / "released"
AGENTS = RELEASE / "business_agent"
_EXPECTED_INDEX = "0f231ae5092e87ef8b13c4c44d82c0be22cfbea0f760f5d9e9873c5649f9c958"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _index():
    file = ROOT / "runtime-index.json"
    if file.is_symlink() or _sha(file) != _EXPECTED_INDEX:
        raise RuntimeError("runtime index mismatch")
    return json.loads(file.read_bytes())


INDEX = _index()


def verify(path, expected):
    return not path.is_symlink() and path == path.resolve() and path.is_file() and _sha(path) == expected


def _package(directory):
    directory = Path(directory)
    if directory != AGENTS / directory.name:
        return None
    return INDEX["packages"].get(directory.name)


def _verified(directory):
    directory = Path(directory)
    item = _package(directory)
    if item is None:
        return False
    try:
        # The upstream checks included test outputs. Require exactly the deployable
        # files here. The builder verified each against the pinned upstream index.
        actual = {f.relative_to(directory).as_posix() for f in directory.rglob("*") if f.is_file()}
        return (actual == set(item["deployed"]) and
                all(verify(directory / f, h) for f, h in item["deployed"].items()))
    except OSError:
        return False


def pinned(worker):
    item = _package(worker.directory)
    if not item or item.get("manifest") != worker.manifest_sha256 or not _verified(worker.directory):
        return False
    if worker.directory.name == "activity_primary_recognition":
        return _verified(AGENTS / "workbench_deepseek")
    return True


def matches_upstream(directory, source_id):
    item = _package(directory)
    return bool(item and item.get("upstreamPythonSha256") == source_id and _verified(directory))


def source_for_hash(source_id):
    if not isinstance(source_id, str):
        return None
    for name, item in INDEX["packages"].items():
        if name.startswith("activity_model_first") and source_id in (
            item.get("deployedPythonSha256"), item["upstreamPythonSha256"]
        ) and _verified(AGENTS / name):
            return AGENTS / name
    return None


def configure(data_dir):
    """Validate the packaged runtime, bind the .108 decoder and return its callable."""
    for name, expected in INDEX["runtime"].items():
        if not verify(ROOT / name, expected):
            raise RuntimeError("runtime file mismatch: " + name)
    roots = [ROOT / "otp-runtime", RELEASE, ROOT / "baseline"]
    for directory in reversed(roots):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    import test_accounts, account_primitives, b_grouping, b_jobs
    import b_model_worker, b_primary_worker, b_model_intake
    test_accounts.EXPECTED = Path(data_dir)
    current = AGENTS / "activity_intake_108"
    b_jobs.ROOT = RELEASE
    b_jobs.WORKER = current / "worker.py"
    b_jobs.SCHEMA_DIR = current / "schemas"
    b_jobs.EXPECTED_ENGINE_VERSION = "1.0.8"
    b_jobs.REGROUP_WORKER = RELEASE / "regroup_wire.py"
    b_jobs.LEGACY_REGROUP_ENABLED = True
    b_grouping.CURRENT_ENGINE = "1.0.8"
    b_grouping.LEGACY_ENGINES = dict(INDEX["legacyEngineVersions"])
    b_model_worker.ModelWorker.pinned = pinned
    b_primary_worker.PrimaryWorker.pinned = pinned
    b_model_intake.ModelIntakeWorker.pinned = pinned
    b_model_intake.source_for_hash = source_for_hash
    sys.path.insert(0, str(current))
    try:
        import api, grouping
        if (Path(api.__file__).resolve() != current / "api.py" or
            Path(grouping.__file__).resolve() != current / "grouping.py" or
            grouping.VERSION != b_grouping.VERSION):
            raise RuntimeError("unexpected intake dependency")
        b_grouping.ENUMERATE = grouping.enumerate_coupon_cases
        b_jobs.XLSX_DECODER = api.decode_material
        return api.decode_material
    finally:
        sys.path.pop(0)
