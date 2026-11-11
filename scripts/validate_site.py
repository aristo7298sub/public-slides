import argparse
import json
import re
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse


ALLOWED_SUFFIXES = {
    ".html", ".css", ".js", ".mjs", ".json", ".svg", ".png", ".jpg",
    ".jpeg", ".webp", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".otf",
    ".mp4", ".webm", ".mp3", ".ogg", ".wav", ".pdf", ".txt",
}
TEXT_SUFFIXES = {".html", ".css", ".js", ".mjs", ".json", ".svg", ".txt"}
FORBIDDEN_NAMES = {"deployment-target.json", "events.jsonl", "credentials.json"}
CREDENTIAL_PATTERN = re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|[?&]sig=)")
CHECKOUT_ACTION = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
PYTHON_ACTION = "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1"
DEPLOY_ACTION = "Azure/static-web-apps-deploy@1a947af9992250f3bc2e68ad0754c0b0c11566c9"
PUBLISH_CONDITION = "${{ github.repository == 'aristo7298sub/public-slides' && github.ref == 'refs/heads/main' && github.actor == 'aristo7298sub' && github.event_name == 'workflow_dispatch' && inputs.confirm_publish == 'PUBLISH' }}"


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.references = []
        self.identifiers = set()
        self.errors = []
        self.language = ""
        self.title = ""
        self.in_title = False
        self.viewport = False
        self.slide_count = 0

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "html":
            self.language = attributes.get("lang", "")
        if tag == "title":
            self.in_title = True
        if tag == "meta" and attributes.get("name", "").lower() == "viewport":
            self.viewport = True
        if tag == "base":
            self.errors.append("base elements are not supported in the public bundle")
        identifier = attributes.get("id")
        if identifier:
            if identifier in self.identifiers:
                self.errors.append("duplicate HTML id")
            self.identifiers.add(identifier)
        if "slide" in attributes.get("class", "").split():
            self.slide_count += 1
        for attribute in ("src", "href", "poster"):
            if attributes.get(attribute):
                self.references.append(attributes[attribute])

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data


def validate_site(directory):
    requested = Path(directory)
    if requested.is_symlink() or not requested.is_dir():
        return ["site must be an existing non-symlink directory"]
    root = requested.resolve()
    errors = []
    pages = {}
    if not (root / "index.html").is_file():
        errors.append("site/index.html is required")
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_symlink():
            errors.append(f"{relative}: symlinks are not permitted")
            continue
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in relative.parts) or path.name in FORBIDDEN_NAMES:
            errors.append(f"{relative}: private configuration or hidden output is not permitted")
            continue
        if path.suffix.lower() not in ALLOWED_SUFFIXES:
            errors.append(f"{relative}: unsupported public file type")
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            content = path.read_text(encoding="utf-8-sig")
        except UnicodeError:
            errors.append(f"{relative}: text assets must use UTF-8")
            continue
        if CREDENTIAL_PATTERN.search(content):
            errors.append(f"{relative}: suspected credential in public output")
        if path.suffix.lower() == ".json":
            try:
                json.loads(content)
            except json.JSONDecodeError:
                errors.append(f"{relative}: invalid JSON asset")
        if path.suffix.lower() != ".html":
            continue
        page = Page()
        page.feed(content)
        pages[path] = page
        if not page.language or not page.title.strip() or not page.viewport:
            errors.append(f"{relative}: HTML lang, title, and viewport metadata are required")
        errors.extend(f"{relative}: {error}" for error in page.errors)
    for path, page in pages.items():
        relative = path.relative_to(root)
        for reference in page.references:
            parsed = urlparse(reference)
            if parsed.scheme in {"https", "http"} or parsed.netloc:
                if parsed.hostname in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
                    errors.append(f"{relative}: localhost reference in public output")
                continue
            if parsed.scheme:
                if parsed.scheme == "mailto" or reference.startswith("data:image/"):
                    continue
                errors.append(f"{relative}: unsupported URL scheme")
                continue
            decoded = unquote(parsed.path)
            destination = path if not decoded else (root / decoded.lstrip("/") if decoded.startswith("/") else path.parent / decoded)
            destination = destination.resolve()
            try:
                destination.relative_to(root)
            except ValueError:
                errors.append(f"{relative}: reference escapes the public site directory")
                continue
            if destination.is_dir():
                destination /= "index.html"
            if not destination.is_file():
                errors.append(f"{relative}: missing local reference")
                continue
            if not parsed.fragment or destination not in pages:
                continue
            target_page = pages[destination]
            fragment = unquote(parsed.fragment)
            is_slide_hash = fragment.isdigit() and int(fragment) < target_page.slide_count
            if fragment not in target_page.identifiers and not is_slide_hash:
                errors.append(f"{relative}: missing local fragment or invalid slide hash")
    return errors


def validate_workflow(path):
    try:
        import yaml
    except ImportError:
        return ["workflow validation requires requirements-dev.txt"]
    try:
        workflow = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return ["workflow could not be read as YAML"]
    if not isinstance(workflow, dict):
        return ["workflow must be a YAML mapping"]
    errors = []
    triggers = workflow.get("on", {})
    if not isinstance(triggers, dict) or set(triggers) != {"workflow_dispatch"}:
        errors.append("only workflow_dispatch may trigger publication")
    confirmation = triggers.get("workflow_dispatch", {}).get("inputs", {}).get("confirm_publish", {}) if isinstance(triggers, dict) else {}
    if confirmation.get("required") is not True or confirmation.get("type") != "string":
        errors.append("manual publication confirmation is required")
    if workflow.get("permissions") != {"contents": "read"}:
        errors.append("workflow permissions must remain contents: read")
    jobs = workflow.get("jobs", {})
    if not isinstance(jobs, dict) or set(jobs) != {"publish"}:
        errors.append("only the guarded publish job is permitted")
    job = jobs.get("publish", {}) if isinstance(jobs, dict) else {}
    if "permissions" in job:
        errors.append("job permissions must not override the read-only workflow scope")
    if " ".join(str(job.get("if", "")).split()) != PUBLISH_CONDITION:
        errors.append("repository, actor, main branch, manual event, and PUBLISH gates must remain intact")
    steps = job.get("steps", [])
    checkout = next((step for step in steps if step.get("uses") == CHECKOUT_ACTION), {})
    setup = next((step for step in steps if step.get("uses") == PYTHON_ACTION), {})
    deploy = next((step for step in steps if step.get("uses") == DEPLOY_ACTION), {})
    if checkout.get("with", {}).get("persist-credentials") is not False:
        errors.append("checkout must use its pinned SHA without persisting credentials")
    if setup.get("with", {}).get("python-version") != "3.12":
        errors.append("validation must use the pinned Python setup action and Python 3.12")
    settings = deploy.get("with", {})
    expected = {
        "azure_static_web_apps_api_token": "${{ secrets.AZURE_STATIC_WEB_APPS_API_TOKEN }}",
        "action": "upload",
        "app_location": "site",
        "api_location": "",
        "output_location": "",
        "skip_app_build": True,
        "skip_api_build": True,
        "production_branch": "main",
    }
    if settings != expected:
        errors.append("deployment must use the pinned action, Secret reference, and complete site-only upload")
    required_checks = {
        "python3 -B scripts/validate_site.py --self-test",
        "python3 -B scripts/validate_site.py --workflow .github/workflows/publish.yml",
        "python3 -B scripts/validate_site.py site",
    }
    if not required_checks.issubset({step.get("run") for step in steps}):
        errors.append("site and workflow checks must run before deployment")
    if deploy and any(step is deploy for step in steps[:-1]):
        errors.append("deployment must follow all validation steps")
    return errors


def sample_html(body="<p>Test</p>"):
    return '<!DOCTYPE html><html lang="en"><head><meta name="viewport" content="width=device-width"><title>Test</title></head><body>' + body + "</body></html>"


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "site"
        self.root.mkdir()
        (self.root / "index.html").write_text(sample_html(), encoding="utf-8")

    def test_valid_site(self):
        self.assertEqual(validate_site(self.root), [])

    def test_missing_asset(self):
        (self.root / "index.html").write_text(sample_html('<img src="missing.png" alt="Test">'), encoding="utf-8")
        self.assertTrue(any("missing local reference" in error for error in validate_site(self.root)))

    def test_path_escape(self):
        (self.root.parent / "private.txt").write_text("private", encoding="utf-8")
        (self.root / "index.html").write_text(sample_html('<a href="../private.txt">Outside</a>'), encoding="utf-8")
        self.assertTrue(any("escapes" in error for error in validate_site(self.root)))

    def test_credential_and_private_configuration(self):
        (self.root / "config.js").write_text("ghp_" + "a" * 40, encoding="utf-8")
        (self.root / "deployment-target.json").write_text("{}", encoding="utf-8")
        errors = validate_site(self.root)
        self.assertTrue(any("suspected credential" in error for error in errors))
        self.assertTrue(any("private configuration" in error for error in errors))

    def test_slide_hash(self):
        deck = self.root / "deck"
        deck.mkdir()
        (deck / "index.html").write_text(sample_html('<section class="slide">First</section>'), encoding="utf-8")
        (self.root / "index.html").write_text(sample_html('<a href="deck/#0">Deck</a>'), encoding="utf-8")
        self.assertEqual(validate_site(self.root), [])
        (self.root / "index.html").write_text(sample_html('<a href="deck/#2">Deck</a>'), encoding="utf-8")
        self.assertTrue(any("invalid slide hash" in error for error in validate_site(self.root)))

    def test_invalid_json(self):
        (self.root / "manifest.json").write_text("{", encoding="utf-8")
        self.assertTrue(any("invalid JSON" in error for error in validate_site(self.root)))

    def test_workflow_rejects_automatic_trigger(self):
        import yaml

        path = self.root.parent / "publish.yml"
        path.write_text(yaml.safe_dump({"on": {"push": {}}, "jobs": {}}), encoding="utf-8")
        self.assertIn("only workflow_dispatch may trigger publication", validate_workflow(path))

    def test_workflow_requires_read_only_permissions(self):
        import yaml

        path = self.root.parent / "publish.yml"
        path.write_text(yaml.safe_dump({"on": {"workflow_dispatch": {}}, "permissions": {"contents": "write"}, "jobs": {}}), encoding="utf-8")
        self.assertIn("workflow permissions must remain contents: read", validate_workflow(path))

    def test_workflow_rejects_unguarded_jobs(self):
        import yaml

        path = self.root.parent / "publish.yml"
        path.write_text(yaml.safe_dump({"on": {"workflow_dispatch": {}}, "jobs": {"publish": {}, "other": {}}}), encoding="utf-8")
        self.assertIn("only the guarded publish job is permitted", validate_workflow(path))

    def test_workflow_rejects_job_permission_override(self):
        import yaml

        path = self.root.parent / "publish.yml"
        path.write_text(yaml.safe_dump({"on": {"workflow_dispatch": {}}, "jobs": {"publish": {"permissions": {"contents": "write"}}}}), encoding="utf-8")
        self.assertIn("job permissions must not override the read-only workflow scope", validate_workflow(path))


def main():
    parser = argparse.ArgumentParser(description="Validate the complete public site bundle before deployment.")
    parser.add_argument("site", nargs="?", default="site")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--workflow")
    arguments = parser.parse_args()
    if arguments.self_test:
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ValidatorTests))
        return 0 if result.wasSuccessful() else 1
    errors = validate_workflow(arguments.workflow) if arguments.workflow else validate_site(arguments.site)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    label = "Workflow" if arguments.workflow else "Public site"
    print(f"{label} checks passed. Human content review and rendered-layout checks are still required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
